#!/usr/bin/env python3
"""把暂存库 `存飞书/` 里的剪藏传进飞书知识库「全文剪藏」，核对无误后把本地那份移进废纸篓。

每篇：
1. 准备：读 Markdown，属性区转成「背景信息」小表，图片全部先在本机备好（网络图下载、本地图按名找），
   字面的 `<` `~` 转义，相邻格式之间的空格换成不换行空格（飞书会吞掉）。按字数定去「短篇」还是「单篇」。
2. 上传：`lark-cli docs +create --doc-format markdown`，标题用文件名（不含扩展名）。
3. 核对：回读飞书文档，比标题、所在位置、正文（逐行比对汉字、字母、数字）和图片数。
4. 全部对上才 `trash` 本地文件（文件夹形式的剪藏整个文件夹一起）；任何一项不对都保留本地，写进报告。

用法：
  python3 archive_to_lark.py scan                     # 只看：列出每篇的字数、去向、图片情况，不上传
  python3 archive_to_lark.py run                      # 上传、核对、删本地
  python3 archive_to_lark.py run --keep-local         # 上传、核对，不删本地
  python3 archive_to_lark.py run --test-folder TOKEN  # 传进云空间测试文件夹，不删本地
  python3 archive_to_lark.py run --item PATH ...      # 只处理指定的文件或文件夹（默认是 存飞书/ 里的全部）

配置：仓库根目录 `.local/clip-archive.json`（见 SKILL.md）。报告写在 staging_dir 下本次任务目录的 report.md。
退出码：0 全部成功；1 有篇没成功（看报告）；2 配置或授权问题，整批没跑。
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from urllib.parse import unquote
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / ".local" / "clip-archive.json"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".avif", ".svg"}
# 飞书直接收的格式；其余格式用 sips 转成 png
FEISHU_IMAGE_KINDS = {"png", "jpeg", "gif", "webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024

# 常见 HTML 标签。剪藏里这些标签按 HTML 处理（飞书认的生效，不认的去掉标签、保留文字）；
# 其他尖括号内容（如 <think>、Vec<T>、a < b）一律转义成字面文字，否则会被飞书当成未知标签吞掉。
HTML_TAGS = {
    "a", "abbr", "article", "aside", "b", "big", "blockquote", "br", "center", "cite", "code", "dd",
    "del", "details", "div", "dl", "dt", "em", "figcaption", "figure", "font", "footer", "h1", "h2",
    "h3", "h4", "h5", "h6", "header", "hr", "i", "img", "ins", "kbd", "li", "mark", "ol", "p", "pre",
    "q", "s", "section", "small", "span", "strike", "strong", "sub", "summary", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "tt", "u", "ul", "video", "audio", "source",
    "picture", "iframe", "label", "input", "button", "nav", "main", "time",
}

META_KEYS = {
    "source": ("source", "url", "link", "来源", "原文链接"),
    "author": ("author", "authors", "作者"),
    "published": ("published", "date", "发布时间", "发布日期"),
    "title": ("title", "标题"),
}

CJK = "㐀-䶿一-鿿豈-﫿぀-ヿ가-힯"
NORM_DROP = re.compile(rf"[^0-9A-Za-z{CJK}]+")
WORD_RE = re.compile(rf"[{CJK}]|[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")

MD_IMAGE = re.compile(
    r"!\[(?P<alt>(?:[^\]\\\n]|\\.)*)\]\(\s*(?P<url><[^>\n]+>|[^)\s]+)(?:\s+\"[^\"\n]*\")?\s*\)"
)
WIKI_EMBED = re.compile(r"!\[\[(?P<name>[^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
HTML_IMG = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"'](?P<url>[^\"']+)[\"'][^>]*>", re.I)
AUTOLINK = re.compile(r"<(?P<url>https?://[^>\s]+)>")
INLINE_CODE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
LINK_DEST = re.compile(r"\]\((?:[^()\s]|\([^()\s]*\))+(?:\s+\"[^\"\n]*\")?\)")
LINK = re.compile(r"\[(?P<text>[^\]\n]*)\]\((?:[^()\s]|\([^()\s]*\))+(?:\s+\"[^\"\n]*\")?\)")
LT = re.compile(r"(?<!\\)<(/?)([A-Za-z][A-Za-z0-9-]*)?")
SINGLE_TILDE = re.compile(r"(?<![~\\])~(?!~)")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
HTML_TAG_ANY = re.compile(r"</?([A-Za-z][A-Za-z0-9-]*)\b[^>]*>")
LIST_MARKER = re.compile(r"^\s*(?:>\s*)*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)")

SPAN = (
    r"(?:\*\*[^*\n]+?\*\*|~~[^~\n]+?~~|<u>.*?</u>|`[^`\n]+`|(?<!\*)\*[^*\s][^*\n]*?\*(?!\*)"
    r"|\[[^\]\n]+\]\([^)\n]+\))"
)
ADJACENT_SPANS = re.compile(rf"({SPAN})( +)(?={SPAN})")

# 双语剪藏的英文原文是灰色字 span（颜色取飞书文字灰），上传时转成飞书的灰色字。
GRAY_OPEN = re.compile(r'<span style="color:\s*#8f959e;?">', re.I)

GENERIC_ALTS = {"", "image", "img", "图片", "图", "picture", "photo"}


# ---------------------------------------------------------------- 纯文本处理（可单测）


def split_frontmatter(text: str) -> tuple[dict, str]:
    """拆出属性区。支持 `---` 包起来的 YAML，以及一些剪藏工具写在开头、不带 `---` 的
    `source: https://…` 几行。只做简单解析：`键: 值` 和 `  - 列表项`。"""
    text = text.lstrip("﻿")
    lines = text.split("\n")
    block: list[str] = []
    rest_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                block = lines[1:i]
                rest_start = i + 1
                break
    elif lines and re.match(r"^source:\s*https?://", lines[0]):
        i = 0
        while i < len(lines) and re.match(r"^[A-Za-z_一-鿿]+:\s*", lines[i]):
            i += 1
        block = lines[:i]
        rest_start = i
    if not block:
        return {}, text
    meta: dict = {}
    key = None
    for line in block:
        m = re.match(r"^([^\s:][^:]*):\s*(.*)$", line)
        if m:
            key = m.group(1).strip().lower()
            val = m.group(2).strip()
            meta[key] = _yaml_scalar(val) if val else []
        elif key is not None and re.match(r"^\s*-\s+", line):
            item = _yaml_scalar(re.sub(r"^\s*-\s+", "", line))
            if not isinstance(meta.get(key), list):
                meta[key] = [meta[key]] if meta.get(key) else []
            meta[key].append(item)
    return meta, "\n".join(lines[rest_start:]).lstrip("\n")


def _yaml_scalar(val: str) -> str:
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return val


def _plain(val) -> str:
    """属性值转成普通文字：去掉双链括号，列表用顿号连起来。"""
    items = val if isinstance(val, list) else [val]
    out = []
    for it in items:
        s = str(it).strip()
        s = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", s)
        s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
        if s:
            out.append(s)
    return "、".join(out)


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("<", "\\<")


def meta_table(meta: dict) -> str:
    """只留来源链接、作者、发布时间，做成和 fulltext-clip 成品开头一样的「背景信息」表。"""

    def pick(name):
        for k in META_KEYS[name]:
            if meta.get(k):
                return _plain(meta[k])
        return ""

    source, author, published, title = pick("source"), pick("author"), pick("published"), pick("title")
    rows = []
    if source:
        url = source.split("、")[0]
        if title:
            rows.append(("原标题", f"[{_cell(title)}]({url})"))
        else:
            rows.append(("来源", f"[原文链接]({url})"))
    if author:
        rows.append(("作者", _cell(author)))
    if published:
        rows.append(("发布时间", _cell(published[:10] if re.match(r"\d{4}-\d{2}-\d{2}", published) else published)))
    if not rows:
        return ""
    lines = ["## 背景信息", "", "| 项目 | 内容 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(lines) + "\n\n"


def _is_image_name(name: str) -> bool:
    return Path(name.split("?")[0]).suffix.lower() in IMAGE_EXTS


def count_source_images(body: str) -> int:
    """原文里的图片数：Markdown 图片、HTML <img>、Obsidian 嵌入的图片文件。代码块里的不算。"""
    n = 0
    for line, in_code in iter_lines(body):
        if in_code:
            continue
        line = INLINE_CODE.sub("", line)
        n += len(MD_IMAGE.findall(line)) + len(HTML_IMG.findall(line))
        n += sum(1 for m in WIKI_EMBED.finditer(line) if _is_image_name(m.group("name")))
    return n


def iter_lines(body: str):
    """逐行给出 (行, 是否在代码块里)。围栏行本身算在代码块里。"""
    fence = None
    for line in body.split("\n"):
        m = FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)[0] * 3
            yield line, True
            continue
        if fence is not None:
            yield line, True
            if m and m.group(1).startswith(fence):
                fence = None
            continue
        yield line, False


class _Protect:
    """把不该被转义的片段（代码、图片、链接地址）先换成占位符，处理完再换回来。"""

    def __init__(self):
        self.items: list[str] = []

    def put(self, s: str) -> str:
        self.items.append(s)
        return f"\x00{len(self.items) - 1}\x00"

    def restore(self, s: str) -> str:
        for _ in range(3):  # 占位符里可能套着占位符
            s = re.sub(r"\x00(\d+)\x00", lambda m: self.items[int(m.group(1))], s)
        return s


def _escape_text(s: str) -> str:
    """字面的 `<`（不是常见 HTML 标签开头的）和单个 `~` 加反斜杠，免得被飞书当成标签或删除线。"""

    def lt(m):
        slash, name = m.group(1), m.group(2) or ""
        if name and name.lower() in HTML_TAGS:
            return "<" + slash + name
        return "\\<" + slash + name

    s = LT.sub(lt, s)
    return SINGLE_TILDE.sub(r"\\~", s)


def transform_body(body: str, resolve) -> tuple[str, list[dict]]:
    """把正文改写成飞书导入用的 Markdown。

    resolve(kind, ref) -> 本地文件名或 None。kind 为 "url"（网络或相对路径）或 "wiki"（Obsidian 嵌入名）。
    返回 (新正文, 图片记录列表)。
    """
    out_lines = []
    images: list[dict] = []

    def image_md(alt: str, kind: str, ref: str) -> str:
        local = resolve(kind, ref)
        rec = {"ref": ref, "kind": kind, "local": local}
        images.append(rec)
        alt_clean = alt.strip()
        if alt_clean.lower() in GENERIC_ALTS or alt_clean == Path(ref.split("?")[0]).name:
            alt_clean = ""
        alt_clean = alt_clean.replace("]", "\\]").replace("[", "\\[")
        if local:
            return f"![{alt_clean}](@./{local})"
        label = alt_clean or Path(ref.split("?")[0]).name or "图片"
        if kind == "url" and re.match(r"https?://", ref):
            return f"[〔图片未上传〕{label}]({ref})"
        return f"〔图片缺失：{_cell(ref)}〕"

    for line, in_code in iter_lines(body):
        if in_code:
            out_lines.append(line)
            continue
        p = _Protect()
        line = INLINE_CODE.sub(lambda m: p.put(m.group(0)), line)
        line = MD_IMAGE.sub(lambda m: p.put(image_md(m.group("alt"), "url", m.group("url").strip("<>"))), line)
        line = HTML_IMG.sub(lambda m: p.put(image_md("", "url", m.group("url"))), line)

        def wiki(m):
            name = m.group("name").strip()
            if _is_image_name(name):
                return p.put(image_md("", "wiki", name))
            return m.group(0)

        line = WIKI_EMBED.sub(wiki, line)
        line = AUTOLINK.sub(lambda m: p.put(f"[{m.group('url')}]({m.group('url')})"), line)
        line = LINK_DEST.sub(lambda m: p.put(m.group(0)), line)
        line = _escape_text(line)
        line = GRAY_OPEN.sub('<span text-color="gray">', line)
        line = p.restore(line)
        line = ADJACENT_SPANS.sub(lambda m: m.group(1) + " " * len(m.group(2)), line)
        out_lines.append(line)
    return "\n".join(out_lines), images


def count_words(body: str) -> int:
    """字数：汉字（含日文假名、韩文）逐个算，英文和数字按词算。图片、链接地址、HTML 标签不算。"""
    s = WIKI_EMBED.sub(" ", MD_IMAGE.sub(" ", body))
    s = HTML_IMG.sub(" ", s)
    s = LINK.sub(lambda m: " " + m.group("text") + " ", s)
    s = HTML_TAG_ANY.sub(" ", s)
    s = re.sub(r"https?://\S+", " ", s)
    return len(WORD_RE.findall(s))


def norm(s: str) -> str:
    return NORM_DROP.sub("", s)


def source_check_lines(body: str) -> list[tuple[str, str]]:
    """原文每一行的「可比文字」：去掉 Markdown 记号、图片、链接地址、HTML 标签后只留汉字、字母、数字。
    返回 [(原行, 可比文字)]，可比文字为空的行不收。"""
    out = []
    for line, in_code in iter_lines(body):
        if in_code and FENCE.match(line):
            continue  # 围栏行（含语言名）飞书不显示
        s = line
        if not in_code:
            s = LIST_MARKER.sub("", s)
            s = re.sub(r"^\s*#{1,6}\s+", "", s)
            s = WIKI_EMBED.sub(lambda m: m.group(0) if not _is_image_name(m.group("name")) else "", s)
            s = MD_IMAGE.sub("", s)
            s = HTML_IMG.sub("", s)
            s = AUTOLINK.sub(lambda m: m.group("url"), s)
            s = LINK.sub(lambda m: m.group("text"), s)
            s = HTML_TAG_ANY.sub(lambda m: "" if m.group(1).lower() in HTML_TAGS else m.group(0), s)
            s = html.unescape(s)
        n = norm(s)
        if n:
            out.append((line, n))
    return out


def feishu_text(xml: str) -> str:
    """飞书回读的 XML → 可比文字。标题、图片说明等属性不算。"""
    s = re.sub(r"<title>.*?</title>", "", xml, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return norm(s)


def check_text(body: str, xml: str) -> list[str]:
    """逐行确认原文的文字都在飞书里、顺序一致。返回找不到的原行（截断），空列表表示全部对上。"""
    dst = feishu_text(xml)
    pos = 0
    missing = []
    for line, n in source_check_lines(body):
        i = dst.find(n, pos)
        if i >= 0:
            pos = i + len(n)
            continue
        if dst.find(n) >= 0:  # 顺序有出入，但文字在
            continue
        missing.append(line.strip()[:80])
    return missing


def count_feishu_images(xml: str) -> int:
    return len(re.findall(r"<img\b", xml))


def feishu_title(xml: str) -> str:
    m = re.search(r"<title>(.*?)</title>", xml, re.S)
    return html.unescape(m.group(1)).strip() if m else ""


# ---------------------------------------------------------------- 图片


def sniff_image(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if data[4:12] in (b"ftypavif", b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "heic"
    return None


def download(url: str, timeout: int = 30) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}
    if "qpic.cn" in url or "weixin" in url:
        headers["Referer"] = "https://mp.weixin.qq.com/"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(MAX_IMAGE_BYTES + 1)


class ImageResolver:
    """把图片备到本篇的工作目录里，返回文件名。失败时返回 None，并在 errors 里记下原因。"""

    def __init__(self, work: Path, md_dir: Path, item_root: Path, vault: Path | None):
        self.work = work
        self.md_dir = md_dir
        self.item_root = item_root
        self.vault = vault
        self.n = 0
        self.errors: list[str] = []

    def __call__(self, kind: str, ref: str) -> str | None:
        try:
            if kind == "url" and re.match(r"https?://", ref):
                data = download(ref)
            else:
                path = self._find_local(ref, kind)
                if path is None:
                    self.errors.append(f"找不到本地图片：{ref}")
                    return None
                data = path.read_bytes()
            return self._save(data, ref)
        except Exception as e:  # noqa: BLE001 — 任何失败都只记录，这张图算没传上
            self.errors.append(f"图片下载或处理失败：{ref}（{type(e).__name__}: {e}）")
            return None

    def _find_local(self, ref: str, kind: str) -> Path | None:
        name = unquote(ref.split("?")[0])
        cands = []
        if kind == "url":
            cands.append((self.md_dir / name))
        base = Path(name).name
        roots = [self.item_root] + ([self.vault] if self.vault else [])
        for c in cands:
            if c.is_file():
                return c
        for root in roots:
            if root.is_file():
                continue
            for p in root.rglob(base):
                if p.is_file() and ".obsidian" not in p.parts and ".trash" not in p.parts:
                    return p
        return None

    def _save(self, data: bytes, ref: str) -> str | None:
        if len(data) > MAX_IMAGE_BYTES:
            self.errors.append(f"图片超过 20MB：{ref}")
            return None
        kind = sniff_image(data)
        self.n += 1
        if kind in FEISHU_IMAGE_KINDS:
            ext = "jpg" if kind == "jpeg" else kind
            name = f"img_{self.n:03d}.{ext}"
            (self.work / name).write_bytes(data)
            return name
        if kind in ("bmp", "tiff", "heic"):
            raw = self.work / f"img_{self.n:03d}.{kind}"
            raw.write_bytes(data)
            name = f"img_{self.n:03d}.png"
            r = subprocess.run(["sips", "-s", "format", "png", str(raw), "--out", str(self.work / name)],
                               capture_output=True, text=True)
            if r.returncode == 0 and (self.work / name).is_file():
                return name
        head = data[:200].decode("utf-8", "replace").strip().replace("\n", " ")[:60]
        self.errors.append(f"不是飞书能收的图片格式：{ref}（开头：{head!r}）")
        return None


# ---------------------------------------------------------------- 飞书


class LarkError(Exception):
    def __init__(self, msg: str, auth: bool = False):
        super().__init__(msg)
        self.auth = auth


def lark(args: list[str], cwd: Path | None = None, timeout: int = 600) -> dict:
    r = subprocess.run(["lark-cli", *args, "--as", "user"], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    raw = r.stdout or r.stderr
    i = raw.find("{")
    try:
        d = json.loads(raw[i:]) if i >= 0 else {}
    except json.JSONDecodeError:
        d = {}
    if not d or not d.get("ok", False):
        err = d.get("error") or {}
        msg = err.get("message") or (r.stderr or r.stdout).strip()[-400:]
        auth = err.get("type") == "authorization" or any(
            k in msg for k in ("token_expired", "need_user_authorization", "refresh_token", "missing_scope", "token_missing"))
        raise LarkError(msg, auth=auth)
    return d.get("data") or {}


def list_wiki_children(space_id: str, parent: str) -> list[dict]:
    d = lark(["wiki", "+node-list", "--space-id", space_id, "--parent-node-token", parent,
              "--page-all", "--page-limit", "0"])
    return [{"token": n.get("obj_token"), "title": n.get("title"), "type": n.get("obj_type"),
             "url": f"https://my.feishu.cn/wiki/{n.get('node_token')}"} for n in d.get("nodes") or []]


def list_drive_folder(folder: str) -> list[dict]:
    out, page = [], ""
    while True:
        params = {"folder_token": folder, "page_size": 200}
        if page:
            params["page_token"] = page
        d = lark(["drive", "files", "list", "--params", json.dumps(params)])
        for f in d.get("files") or []:
            out.append({"token": f.get("token"), "title": f.get("name"), "type": f.get("type"), "url": f.get("url")})
        if not d.get("has_more"):
            return out
        page = d.get("next_page_token") or d.get("page_token") or ""
        if not page:
            return out


# ---------------------------------------------------------------- 主流程


@dataclass
class Item:
    path: Path              # 用户的文件或文件夹（删的就是它）
    md: Path                # 正文 Markdown
    title: str
    extra_meta: dict = field(default_factory=dict)
    # 结果
    words: int = 0
    dest: str = ""          # "short" / "long"
    src_images: int = 0
    ready_images: int = 0
    feishu_images: int | None = None
    missing_lines: list[str] = field(default_factory=list)
    image_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    doc_id: str = ""
    url: str = ""
    manual_delete: bool = False  # 核对通过，但本地文件 macOS 不让挪，要用户手动删
    status: str = ""        # 成功并已删本地 / 成功（保留本地）/ 未删本地：原因 / 跳过：原因 / 失败：原因
    ok: bool = False


def discover(src: Path) -> tuple[list[Item], list[str]]:
    items, skipped = [], []
    for p in sorted(src.iterdir(), key=lambda x: x.name):
        if p.name.startswith("."):
            continue
        it = make_item(p)
        if it:
            items.append(it)
        else:
            skipped.append(p.name)
    return items, skipped


def make_item(p: Path) -> Item | None:
    if p.is_file() and p.suffix.lower() in (".md", ".markdown"):
        return Item(path=p, md=p, title=p.stem)
    if p.is_dir():
        idx = p / "index.json"
        meta = {}
        md = None
        if idx.is_file():
            try:
                j = json.loads(idx.read_text("utf-8"))
                if j.get("mainPath") and (p / j["mainPath"]).is_file():
                    md = p / j["mainPath"]
                if j.get("link"):
                    meta["source"] = j["link"]
            except (json.JSONDecodeError, OSError):
                pass
        if md is None:
            mds = [x for x in p.glob("*.md")]
            if len(mds) == 1:
                md = mds[0]
        if md:
            return Item(path=p, md=md, title=p.name, extra_meta=meta)
    return None


def prepare(it: Item, work: Path, vault: Path | None, short_max: int) -> str:
    text = it.md.read_text("utf-8")
    meta, body = split_frontmatter(text)
    for k, v in it.extra_meta.items():
        meta.setdefault(k, v)
    it.words = count_words(body)
    it.dest = "short" if it.words <= short_max else "long"
    it.src_images = count_source_images(body)
    work.mkdir(parents=True, exist_ok=True)
    resolver = ImageResolver(work, it.md.parent, it.path if it.path.is_dir() else it.md.parent, vault)
    new_body, images = transform_body(body, resolver)
    it.ready_images = sum(1 for x in images if x["local"])
    it.image_errors = resolver.errors
    content = meta_table(meta) + new_body
    (work / "content.md").write_text(content, "utf-8")
    (work / "source_body.md").write_text(body, "utf-8")
    return body


def run(args) -> int:
    cfg_path = Path(args.config) if args.config else DEFAULT_CONFIG
    if not cfg_path.is_file():
        print(f"缺少配置文件：{cfg_path}（见 SKILL.md）", file=sys.stderr)
        return 2
    cfg = json.loads(cfg_path.read_text("utf-8"))
    src = Path(cfg["src_dir"])
    vault = Path(cfg["vault_dir"]) if cfg.get("vault_dir") else None
    short_max = int(cfg.get("short_max_words", 800))
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    job = Path(cfg["staging_dir"]) / f"上传存飞书_{stamp}"
    job.mkdir(parents=True, exist_ok=True)

    if args.item:
        items, skipped = [], []
        for s in args.item:
            it = make_item(Path(s).expanduser())
            (items.append(it) if it else skipped.append(s))
    else:
        if not src.is_dir():
            print(f"找不到待上传目录：{src}", file=sys.stderr)
            return 2
        items, skipped = discover(src)

    test_mode = bool(args.test_folder)
    keep_local = args.keep_local or test_mode or args.cmd == "scan"
    parents = ({"short": args.test_folder, "long": args.test_folder} if test_mode
               else {"short": cfg["short_node"], "long": cfg["long_node"]})

    def children(dest):
        if test_mode:
            return list_drive_folder(parents[dest])
        return list_wiki_children(cfg["space_id"], parents[dest])

    try:
        listed = {}
        for d in ("short", "long"):
            if parents[d] not in listed:
                listed[parents[d]] = children(d)
        existing = {d: listed[parents[d]] for d in ("short", "long")}
    except LarkError as e:
        print(f"读飞书目录失败：{e}", file=sys.stderr)
        return 2

    for n, it in enumerate(items, 1):
        work = job / f"{n:02d}"
        body = prepare(it, work, vault, short_max)
        dup = [(d, c) for d in {parents["short"]: "short", parents["long"]: "long"}.values() for c in existing[d]
               if c["title"] == it.title and c["type"] == "docx"]
        if dup:
            # 以前传过（比如上次核对通过但本地没删掉）：拿飞书那份和本地核对，一致才删本地，不重复上传。
            where, node = dup[0]
            it.doc_id, it.url = node["token"], node["url"]
            if args.cmd == "scan":
                it.status = "（只看）飞书里已有同名文档"
                continue
            verify(it, work, body, where, children)
            if it.ok:
                finish_local(it, keep_local, prefix="飞书里已有、核对一致")
            else:
                it.status = it.status.replace("未删本地：", "未删本地：飞书里已有同名文档，但") or "未删本地：飞书里已有同名文档"
            continue
        if args.cmd == "scan":
            it.status = "（只看，未上传）"
            continue
        parent = parents[it.dest]
        try:
            d = lark(["docs", "+create", "--doc-format", "markdown", "--title", it.title,
                      "--content", "@./content.md", "--parent-token", parent], cwd=work)
        except LarkError as e:
            if e.auth:
                it.status = f"失败：飞书授权问题（{e}）"
                print(f"飞书授权问题，停止：{e}", file=sys.stderr)
                write_report(job, items, skipped, args, test_mode)
                return 2
            it.status = f"失败：建文档出错（{e}）"
            continue
        doc = d.get("document") or {}
        it.doc_id = doc.get("document_id", "")
        it.url = doc.get("url", "")
        it.warnings = [str(w) for w in (d.get("warnings") or [])]
        (work / "create.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
        verify(it, work, body, it.dest, children)
        if it.ok:
            finish_local(it, keep_local, prefix="成功")

    write_report(job, items, skipped, args, test_mode)
    print(job / "report.md")
    return 0 if items and all(it.ok or it.status.startswith("（只看") for it in items) else 1


def move_to_trash(path: Path) -> tuple[bool, str]:
    """移进废纸篓（系统自带的 trash，可从废纸篓恢复）。在 Obsidian 里打开过的文件常带 com.apple.macl，
    macOS 不让别的程序挪动，会被拒；用户 2026-11-18 定的做法是这类文件由用户手动删，脚本不绕。"""
    r = subprocess.run(["/usr/bin/trash", str(path)], capture_output=True, text=True)
    if r.returncode == 0 and not path.exists():
        return True, ""
    code = re.search(r"Code=(\d+)", r.stderr or r.stdout or "")
    return False, f"macOS 不让挪（Code={code.group(1)}）" if code else (r.stderr or r.stdout).strip()[-80:]


def finish_local(it: Item, keep_local: bool, prefix: str) -> None:
    if keep_local:
        it.status = f"{prefix}（保留本地）"
        return
    ok, why = move_to_trash(it.path)
    if ok:
        it.status = f"{prefix}，本地已移进废纸篓"
    else:
        it.ok = False
        it.manual_delete = True
        it.status = f"{prefix}，本地要手动删（{why}）"


def verify(it: Item, work: Path, body: str, dest: str, children) -> None:
    problems = []
    try:
        d = lark(["docs", "+fetch", "--doc", it.doc_id])
        xml = (d.get("document") or {}).get("content", "")
    except LarkError as e:
        it.status = f"未删本地：回读失败（{e}）"
        return
    (work / "fetched.xml").write_text(xml, "utf-8")
    if feishu_title(xml) != it.title:
        problems.append(f"标题不符（飞书：{feishu_title(xml)}）")
    try:
        node = next((c for c in children(dest) if c["token"] == it.doc_id), None)
    except LarkError as e:
        node = None
        problems.append(f"列目录失败（{e}）")
    if node is None:
        problems.append("在目标文件夹里没找到这篇")
    else:
        it.url = node.get("url") or it.url
    it.feishu_images = count_feishu_images(xml)
    if it.feishu_images != it.src_images:
        problems.append(f"图片数不符（本地 {it.src_images}，飞书 {it.feishu_images}）")
    it.missing_lines = check_text(body, xml)
    if it.missing_lines:
        problems.append(f"有 {len(it.missing_lines)} 行文字在飞书里没找到")
    if problems:
        it.status = "未删本地：" + "；".join(problems)
        it.ok = False
    else:
        it.ok = True


def write_report(job: Path, items: list[Item], skipped: list[str], args, test_mode: bool) -> None:
    dest_name = {"short": "4短篇", "long": "3单篇"}
    lines = [f"# 上传存飞书报告 {job.name}", ""]
    if test_mode:
        lines += [f"测试模式：全部传进云空间测试文件夹 `{args.test_folder}`，本地不删。", ""]
    lines += ["| # | 标题 | 字数 | 去向 | 图片 本地/备好/飞书 | 结果 | 链接 |", "|---|---|---:|---|---|---|---|"]
    for n, it in enumerate(items, 1):
        fi = "—" if it.feishu_images is None else str(it.feishu_images)
        lines.append(f"| {n} | {it.title} | {it.words} | {dest_name.get(it.dest, '')} | "
                     f"{it.src_images}/{it.ready_images}/{fi} | {it.status} | {it.url} |")
    for n, it in enumerate(items, 1):
        if it.image_errors or it.missing_lines or it.warnings:
            lines += ["", f"## {n}. {it.title}"]
            for e in it.image_errors:
                lines.append(f"- 图片：{e}")
            for m in it.missing_lines[:20]:
                lines.append(f"- 飞书里没找到这行：{m}")
            if len(it.missing_lines) > 20:
                lines.append(f"- ……另有 {len(it.missing_lines) - 20} 行")
            for w in it.warnings:
                lines.append(f"- 飞书提示：{w}")
    manual = [it for it in items if it.manual_delete]
    if manual:
        lines += ["", "## 要你手动删的本地文件", "",
                  "这几篇已传进飞书并核对一致，但 macOS 不让脚本挪动本地文件，请在 Obsidian 或 Finder 里删掉：", ""]
        lines += [f"- `{it.path}`" for it in manual]
    if skipped:
        lines += ["", "## 没处理的文件", ""] + [f"- {s}（不是 Markdown 剪藏）" for s in skipped]
    (job / "report.md").write_text("\n".join(lines) + "\n", "utf-8")
    data = [{k: (str(v) if isinstance(v, Path) else v) for k, v in it.__dict__.items()} for it in items]
    (job / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["scan", "run"])
    ap.add_argument("--config")
    ap.add_argument("--item", action="append", help="只处理这个文件或文件夹，可重复")
    ap.add_argument("--keep-local", action="store_true", help="核对通过也不删本地")
    ap.add_argument("--test-folder", help="传进这个云空间文件夹（测试用），本地不删")
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
