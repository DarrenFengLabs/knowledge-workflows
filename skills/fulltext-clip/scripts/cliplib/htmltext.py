"""网页 → 元信息 + Markdown 段落块。只用标准库。

思路：用 HTMLParser 建一棵宽松的树，删掉脚本、导航、页脚等，按已知容器或文字密度选正文区域，
再把块级元素转成 Markdown 段落（小标题、列表、引用、表格、图片占位）。抽不准时由模型指出起止段，
脚本截取（见 prep.py select）。
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "param", "track"}
DROP = {"script", "style", "noscript", "svg", "template", "iframe", "form", "button", "nav", "footer", "aside", "select", "textarea", "canvas"}
BLOCK = {
    "p", "div", "section", "article", "main", "header", "li", "ul", "ol", "blockquote", "pre", "table",
    "tr", "h1", "h2", "h3", "h4", "h5", "h6", "figure", "figcaption", "hr", "dl", "dt", "dd", "body", "root",
}
CONTAINER_HINTS = [
    ("id", "js_content"),                 # 微信公众号
    ("class", "available-content"),       # Substack
    ("class", "body markup"),
    ("itemprop", "articleBody"),
    ("class", "RichText"),                # 知乎
    ("class", "post-content"),
    ("class", "entry-content"),
    ("class", "article-content"),
    ("class", "article-body"),
    ("class", "content-body"),
    ("id", "article"),
]
PUNCT_EDGE = set(" \t\n\u3000。，、；：？！…—·（）【】「」『』《》〈〉\"'“”‘’,.;:?!()[]")


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or {})
        self.children = []
        self.parent = parent

    def text(self) -> str:
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.text())
        return "".join(out)

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse(html: str) -> Node:
    b = _Builder()
    b.feed(html)
    b.close()
    return b.root


# ---------- 元信息 ----------

def _meta_map(root: Node) -> dict:
    out = {}
    for n in root.iter():
        if n.tag == "meta":
            key = n.attrs.get("property") or n.attrs.get("name") or n.attrs.get("itemprop")
            if key and n.attrs.get("content") and key.lower() not in out:
                out[key.lower()] = n.attrs["content"].strip()
    return out


def _json_ld(root: Node) -> list[dict]:
    items = []
    for n in root.iter():
        if n.tag == "script" and "ld+json" in (n.attrs.get("type") or ""):
            try:
                data = json.loads(n.text())
            except (ValueError, TypeError):
                continue
            stack = data if isinstance(data, list) else [data]
            while stack:
                d = stack.pop(0)
                if isinstance(d, dict):
                    if "@graph" in d and isinstance(d["@graph"], list):
                        stack.extend(d["@graph"])
                    items.append(d)
    return items


def _name(v):
    if isinstance(v, list):
        return "、".join(filter(None, (_name(x) for x in v)))
    if isinstance(v, dict):
        return v.get("name")
    return v


def page_meta(html: str, root: Node | None = None) -> dict:
    root = root or parse(html)
    meta = _meta_map(root)
    ld = next((d for d in _json_ld(root) if str(d.get("@type", "")).lower().endswith(("article", "posting", "episode", "report"))), {})
    title_tag = next((n.text().strip() for n in root.iter() if n.tag == "title"), "")
    h1 = next((n.text().strip() for n in root.iter() if n.tag == "h1"), "")
    time_tag = next((n.attrs.get("datetime") for n in root.iter() if n.tag == "time" and n.attrs.get("datetime")), None)
    canonical = next((n.attrs.get("href") for n in root.iter() if n.tag == "link" and "canonical" in (n.attrs.get("rel") or "")), None)
    html_lang = next((n.attrs.get("lang") for n in root.iter() if n.tag == "html"), None)
    return {
        "title": meta.get("og:title") or meta.get("twitter:title") or ld.get("headline") or h1 or title_tag,
        "site": meta.get("og:site_name") or _name(ld.get("publisher")),
        "author": meta.get("author") or meta.get("article:author") or _name(ld.get("author")),
        "published": meta.get("article:published_time") or ld.get("datePublished") or meta.get("pubdate")
        or meta.get("publishdate") or meta.get("date") or time_tag,
        "description": meta.get("og:description") or meta.get("description"),
        "url": meta.get("og:url") or canonical,
        "lang": html_lang,
        "og_type": meta.get("og:type"),
        "has_media": bool(meta.get("og:video") or meta.get("og:audio")),
    }


# ---------- 正文区域 ----------

def _clean(root: Node) -> None:
    for n in list(root.iter()):
        n.children = [c for c in n.children if not (isinstance(c, Node) and c.tag in DROP)]


def _match(n: Node, attr: str, value: str) -> bool:
    v = n.attrs.get(attr) or ""
    if attr == "class":
        return all(part in v.split() for part in value.split())
    return v == value


def _score(n: Node) -> float:
    p_text = sum(len(c.text().strip()) for c in n.iter() if c.tag == "p")
    link_text = sum(len(c.text()) for c in n.iter() if c.tag == "a")
    return p_text - 0.5 * link_text


def main_region(root: Node) -> Node:
    _clean(root)
    for attr, value in CONTAINER_HINTS:
        for n in root.iter():
            if _match(n, attr, value) and len(n.text().strip()) > 200:
                return n
    articles = [n for n in root.iter() if n.tag == "article"]
    if articles:
        return max(articles, key=lambda n: len(n.text()))
    candidates = [n for n in root.iter() if n.tag in {"div", "section", "main", "body"}]
    best = max(candidates, key=_score, default=root)
    return best if _score(best) > 0 else root


# ---------- 转 Markdown ----------

def _fix_bold(inner: str) -> str:
    """把粗体两端的空格和标点移到星号外，避免飞书/Obsidian 不渲染。"""
    core = inner
    lead = trail = ""
    while core and core[0] in PUNCT_EDGE:
        lead += core[0]
        core = core[1:]
    while core and core[-1] in PUNCT_EDGE:
        trail = core[-1] + trail
        core = core[:-1]
    return f"{lead}**{core}**{trail}" if core else inner


class _Md:
    def __init__(self, heading_shift: int):
        self.blocks: list[str] = []
        self.buf: list[str] = []
        self.shift = heading_shift

    def flush(self, prefix: str = ""):
        text = re.sub(r"[ \t\u00a0\u200b\ufeff]+", " ", "".join(self.buf)).strip()
        self.buf = []
        if text:
            self.blocks.append(prefix + text)

    def inline(self, n) -> str:
        if isinstance(n, str):
            return n
        tag = n.tag
        if tag in DROP:
            return ""
        if tag == "br":
            return "\n"
        if tag == "img":
            alt = (n.attrs.get("alt") or "").strip()
            return f"〔图：{alt}〕" if alt else "〔图〕"
        inner = "".join(self.inline(c) for c in n.children)
        if tag in {"strong", "b"} and inner.strip():
            return _fix_bold(inner.strip())
        if tag == "a":
            href = n.attrs.get("href") or ""
            label = inner.strip()
            if href.startswith("http") and label and label != href and "〔图" not in label:
                return f"[{label}]({href})"
            return inner
        if tag == "code":
            return f"`{inner}`" if "\n" not in inner else inner
        return inner

    def walk(self, n):
        if isinstance(n, str):
            self.buf.append(n)
            return
        tag = n.tag
        if tag in DROP:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.flush()
            level = min(5, max(3, int(tag[1]) + self.shift))
            text = re.sub(r"\s+", " ", "".join(self.inline(c) for c in n.children)).strip()
            text = text.replace("**", "")
            if text:
                self.blocks.append("#" * level + " " + text)
            return
        if tag in {"ul", "ol"}:
            self.flush()
            k = 0
            for c in n.children:
                if isinstance(c, Node) and c.tag == "li":
                    k += 1
                    text = re.sub(r"\s+", " ", "".join(self.inline(x) for x in c.children)).strip()
                    if text:
                        self.blocks.append((f"{k}. " if tag == "ol" else "- ") + text)
            return
        if tag == "blockquote":
            self.flush()
            sub = _Md(self.shift)
            for c in n.children:
                sub.walk(c)
            sub.flush()
            for b in sub.blocks:
                self.blocks.append("\n".join("> " + line for line in b.split("\n")))
            return
        if tag == "pre":
            self.flush()
            self.blocks.append("```\n" + n.text().strip("\n") + "\n```")
            return
        if tag == "table":
            self.flush()
            rows = []
            for tr in (x for x in n.iter() if x.tag == "tr"):
                cells = [re.sub(r"\s+", " ", "".join(self.inline(c) for c in td.children)).strip().replace("|", "／")
                         for td in tr.children if isinstance(td, Node) and td.tag in {"td", "th"}]
                if cells:
                    rows.append(cells)
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
                lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self.blocks.append("\n".join(lines))
            return
        if tag == "img":
            self.flush()
            self.blocks.append(self.inline(n))
            return
        if tag in {"figcaption"}:
            self.flush()
            text = n.text().strip()
            if text:
                self.blocks.append(f"〔图注：{text}〕")
            return
        if tag == "hr":
            self.flush()
            return
        if tag in BLOCK:
            self.flush()
            for c in n.children:
                self.walk(c)
            self.flush()
            return
        self.buf.append(self.inline(n))


def to_blocks(region: Node) -> list[str]:
    levels = [int(n.tag[1]) for n in region.iter() if re.fullmatch(r"h[1-6]", n.tag)]
    shift = 3 - min(levels) if levels else 0
    md = _Md(shift)
    md.walk(region)
    md.flush()
    out = []
    for b in md.blocks:
        # 段内的 <br> 换行：中文长段拆成多段，列表与引用保持原样
        if "\n" in b and not b.startswith(("- ", "> ", "```", "|")) and not re.match(r"\d+\. ", b):
            parts = [p.strip() for p in b.split("\n") if p.strip()]
            out.extend(parts)
        else:
            out.append(b)
    dedup = []
    for b in out:
        if b in {"〔图〕"} and dedup and dedup[-1] == "〔图〕":
            continue
        if not dedup or dedup[-1] != b:
            dedup.append(b)
    return dedup


def extract(html: str) -> tuple[dict, list[str], int]:
    """返回 (元信息, 正文块, 全页可见文字数)。"""
    root = parse(html)
    meta = page_meta(html, root)
    region = main_region(root)  # 会先删掉脚本、导航等
    total = len(re.sub(r"\s", "", root.text()))
    return meta, to_blocks(region), total
