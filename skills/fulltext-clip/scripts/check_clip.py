#!/usr/bin/env python3
"""全文剪藏成品的质量检查。

用法:
  python3 scripts/check_clip.py --job-dir <任务目录>          # 检查 final.md，并对照 source.md、sheet.json、各块产出
  python3 scripts/check_clip.py 成品.md [--source 原稿.md] [--mode 精修|整理|翻译] [--json]

查四类问题：
  没整理   残留平台说话人标签、段落标记、时间码、口头禅、小写英文缩写（识别错误），字数几乎没变
  删多了   字数比例偏低（全篇和每块分别算；只出中文的翻译按“汉字数 / 英文词数”算）
  标错人   人名加粗；单人内容出现 🎙️；无标签原稿里出现“名字：”或“说话人N”；同一段既有 🎙️ 又有名字
  格式     结构顺序、背景表必填项、一级标题、双语配对、强调标记、下划线密度；列出全部〔?〕
“违规”要修，“提醒”要看一眼再决定。有违规时退出码为 1。
强调标记的判定与 notes-with-media/scripts/check_format.py 同源，改一处同步另一处。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cliplib.analyze import FILLERS_ZH, LOWER_OK, filler_rate  # noqa: E402
from cliplib.common import count_cjk, read_json, read_segments, strip_markup, update_status, write_json  # noqa: E402

META_SECTIONS = ["背景信息", "全文大纲", "术语速查", "正文", "补充细节", "拟入表"]
BG_REQUIRED = ["原标题", "来源", "发布时间", "规模"]
RAW_LABEL = re.compile(r"\d+号讲话人|讲话人\s?\d+|(?<![A-Za-z])Speaker\s?\d+|发言人\s?\d+")
TIMECODE = re.compile(r"(?<![\d:])\d{1,2}:\d{2}:\d{2}(?![\d:])")
BOLD_NAME = re.compile(r"^(?:\*\*[^*\n]{1,24}[：:]\*\*|\*\*[^*\n]{1,24}\*\*[：:])")
LABEL_START = re.compile(r"^(?:🎙️\s*)?([^\s：:*<>|#\-]{1,12})：")
NUMBERED = re.compile(r"^说话人\d+：")
HOST_CUE = re.compile(r"欢迎|有请|介绍|今天的嘉宾|本期|下一个|接下来|我们来|我们聊|聊聊|说说|讲讲|谢谢|感谢|回到|最后|开始")
ROLE_WORD = re.compile(r"主持人?|主播|嘉宾|观众[A-Z]?|提问|说话人\d*|讲话人\s?\d*|Speaker\s?\d*|Interviewer|Host|Guest")

# ---------- 强调标记（与 notes-with-media/scripts/check_format.py 同源） ----------

BAD = set(' \t\n　。，、；：？！…—·（）【】「」『』《》〈〉') | set(['"', "'", '“', '”', '‘', '’'])
FENCE = re.compile(r'^\s*(```|~~~)')
INLINE_CODE = re.compile(r'`[^`\n]*`')
LIST_STAR = re.compile(r'^(\s*)\*(?=\s)')
RULE = re.compile(r'^\s*(\*\s*){3,}$')
BOLD = re.compile(r'\*\*([^*\n]+?)\*\*')


def _bad_edge(inner):
    return not inner or inner[0] in BAD or inner[-1] in BAD


def emphasis_problems(text: str) -> list[tuple[int, str, str]]:
    problems = []
    in_code = False
    for no, raw in enumerate(text.splitlines(), 1):
        if FENCE.match(raw):
            in_code = not in_code
            continue
        if in_code or RULE.match(raw):
            continue
        line = raw.replace('\\*', '')
        line = INLINE_CODE.sub('C', line)
        line = LIST_STAR.sub(r'\1-', line)

        def bold(m):
            if _bad_edge(m.group(1)):
                problems.append((no, '加粗失效', m.group(1)[:32]))
            return 'B'

        line = BOLD.sub(bold, line)
        if '**' in line:
            problems.append((no, '加粗未闭合', raw.strip()[:32]))
            line = line.replace('**', '')
        stars = [i for i, ch in enumerate(line) if ch == '*']
        for a, b in zip(stars[0::2], stars[1::2]):
            if _bad_edge(line[a + 1:b]):
                problems.append((no, '斜体失效', line[a + 1:b][:32]))
        if len(stars) % 2:
            problems.append((no, '斜体未闭合', raw.strip()[:32]))
    return problems


# ---------- 解析成品 ----------

def split_sections(text: str) -> list[dict]:
    sections = []
    cur = {"name": None, "start": 1, "lines": []}
    for no, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            sections.append(cur)
            cur = {"name": m.group(1).strip(), "start": no, "lines": []}
        else:
            cur["lines"].append((no, line))
    sections.append(cur)
    return sections


def paragraphs(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    out, buf, start, in_code = [], [], None, False
    for no, line in lines:
        if FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        if line.strip():
            if not buf:
                start = no
            buf.append(line)
        elif buf:
            out.append((start, "\n".join(buf)))
            buf = []
    if buf:
        out.append((start, "\n".join(buf)))
    return out


def is_prose(p: str) -> bool:
    return not p.startswith(("#", "|", "```", "- ", "> ", "〔图")) and not re.match(r"\d+\. ", p)


# 书面语里应当大写的缩写；小写出现多半是语音识别没纠正（如 ai、vc、ceo、okr）
MUST_UPPER = {
    "ai", "agi", "ceo", "cfo", "cto", "coo", "vc", "pe", "ipo", "llm", "gpt", "api", "sdk", "cli", "okr", "kpi",
    "hr", "ui", "ux", "seo", "sop", "mbti", "phd", "aws", "saas", "gpu", "cpu", "tob", "toc", "b2b", "b2c",
    "gmv", "roi", "dau", "mau", "arr", "iq", "eq", "nba", "mba", "ppt", "pdf", "sel",
}


def short_latin(text: str) -> tuple[dict, dict]:
    """返回 (应大写却小写的缩写, 其他 2–3 个字母的小写词)，只看中文语境，跳过链接和代码。"""
    text = re.sub(r"https?://\S+|`[^`]*`|\[[^\]]*\]\([^)]*\)", " ", text)
    upper, other = {}, {}
    for m in re.finditer(r"(?<![A-Za-z.])([a-z]{2,4})(?![A-Za-z.])", text):
        w = m.group(1)
        window = text[max(0, m.start() - 6):m.end() + 6]
        if count_cjk(window) < 3:
            continue
        if w in MUST_UPPER:
            upper[w] = upper.get(w, 0) + 1
        elif len(w) <= 3 and w not in LOWER_OK:
            other[w] = other.get(w, 0) + 1
    return upper, other


# ---------- 检查 ----------

def check(text: str, ctx: dict) -> dict:
    items = []

    def add(level, code, msg, line=None, excerpt=""):
        items.append({"level": level, "code": code, "message": msg, "line": line, "excerpt": excerpt[:40]})

    lines = text.splitlines()
    sections = split_sections(text)
    names = [s["name"] for s in sections if s["name"]]
    first = next((l for l in lines if l.strip()), "")
    if first.strip() != "## 背景信息":
        add("违规", "S1", "第一行应是 `## 背景信息`", 1, first)
    for no, line in enumerate(lines, 1):
        if re.match(r"^# ", line):
            add("违规", "S2", "不写一级标题（标题在文件名里）", no, line)
    bg = next((s for s in sections if s["name"] == "背景信息"), None)
    if bg:
        rows = {m.group(1).strip() for _, l in bg["lines"] for m in [re.match(r"^\|\s*([^|]+?)\s*\|", l)] if m}
        missing = [k for k in BG_REQUIRED if k not in rows]
        if missing:
            add("违规", "S3", "背景信息应为两列表格，缺：" + "、".join(missing), bg["start"])
    order = [n for n in names if n in META_SECTIONS]
    if order != sorted(order, key=META_SECTIONS.index):
        add("提醒", "S4", "栏目顺序应为：" + " → ".join(META_SECTIONS), None, " / ".join(order))
    has_body_section = "正文" in names
    if ctx.get("job") and not has_body_section:
        add("违规", "S5", "缺 `## 正文`")
    body_secs = [s for s in sections if s["name"] == "正文"] if has_body_section else \
        [s for s in sections if s["name"] and s["name"] not in META_SECTIONS]
    body_lines = [x for s in body_secs for x in s["lines"]]
    body_paras = paragraphs(body_lines)
    body_text = "\n\n".join(p for _, p in body_paras)
    bilingual = "<small>" in body_text
    zh_text = re.sub(r"<small>.*?</small>", "", body_text, flags=re.S)
    zh_plain = strip_markup(zh_text)
    body_chars = len(re.sub(r"\s", "", strip_markup(body_text)))
    zh_cjk = count_cjk(zh_plain)
    if body_chars > 3000 and "全文大纲" not in names:
        add("提醒", "S6", "正文超过 3 千字，应有全文大纲")

    # 没整理
    raw_labels = [(no, m.group(0)) for no, p in body_paras for m in RAW_LABEL.finditer(p)]
    if raw_labels:
        add("违规", "U1", f"残留平台说话人标签 {len(raw_labels)} 处（应按工作单换成名字、说话人N 或 🎙️）", raw_labels[0][0], raw_labels[0][1])
    markers = [(no, p) for no, p in body_paras if re.search(r"〔[sp]\d+", p)]
    if markers:
        add("违规", "U2", f"残留段落标记 {len(markers)} 处", markers[0][0], markers[0][1])
    tcs = [(no, m.group(0)) for no, p in body_paras if not p.startswith("#") for m in TIMECODE.finditer(p)]
    if len(tcs) >= 3:
        add("违规", "U3", f"正文残留时间码 {len(tcs)} 处", tcs[0][0], tcs[0][1])
    metrics = {"body_chars": body_chars, "zh_cjk": zh_cjk, "bilingual": bilingual}
    if zh_cjk > 1000:
        fr = filler_rate(zh_plain)
        metrics["filler_per_1k"] = fr
        src_fr = ctx.get("source_filler")
        limit = max(2.0, 0.5 * src_fr) if src_fr else 2.0
        n_fill = sum(zh_plain.count(f) for f in FILLERS_ZH)
        if fr > limit:
            level = "违规" if n_fill >= 8 else "提醒"
            add(level, "U4", f"口头禅残留 {n_fill} 处，每千字 {fr} 处（上限 {limit}；看 {', '.join(FILLERS_ZH[:6])} 等）")
    if zh_cjk:
        upper, other = short_latin(zh_text)
        metrics["lower_acronyms"] = upper
        if sum(upper.values()) >= 3:
            top = sorted(upper.items(), key=lambda x: -x[1])[:8]
            add("违规", "U5", "应大写的缩写写成了小写，多半是识别错误没纠正：" + "、".join(f"{w}×{n}" for w, n in top))
        if len(other) >= 6:
            top = sorted(other.items(), key=lambda x: -x[1])[:8]
            add("提醒", "U5", "中文里夹着较多小写英文短词，看看是否识别错误：" + "、".join(f"{w}×{n}" for w, n in top))
        runs = re.findall(r"[\u4e00-\u9fff][^。！？；，、.!?;,\n]{150,}", zh_plain)
        if runs:
            add("提醒", "U6", f"有 {len(runs)} 处 150 字以上没有标点", None, runs[0][:30])
    # 字数比例：中文按汉字算（补进去的标点不算字），其他按非空白字符算
    src_chars, src_cjk = ctx.get("source_chars"), ctx.get("source_cjk")
    mode = ctx.get("mode")
    ratio = None
    if mode == "翻译" and not bilingual and ctx.get("source_words") and zh_cjk:
        # 中文汉字数约为英文词数的 1.15–1.87 倍（2026-10 对 18 篇旧双语稿的统计，多数 1.3–1.5）
        per_word = zh_cjk / ctx["source_words"]
        metrics["zh_per_en_word"] = round(per_word, 2)
        if per_word < 0.8:
            add("违规", "U7", f"译文汉字数只有英文词数的 {per_word:.2f} 倍（通常 1.2–1.6），可能漏译或译成了摘要")
        elif per_word < 1.0:
            add("提醒", "U7", f"译文汉字数为英文词数的 {per_word:.2f} 倍，偏低，自查是否漏译")
    elif not bilingual and mode != "翻译":
        if src_cjk and zh_cjk:
            ratio = zh_cjk / src_cjk
        elif src_chars:
            ratio = body_chars / src_chars
    if ratio is not None:
        metrics["ratio"] = round(ratio, 3)
        if mode == "整理":
            if ratio < 0.95:
                add("违规", "U7", f"整理档正文应完整搬入，现为原文的 {ratio:.0%}")
        elif ratio < 0.70:
            add("违规", "U7", f"字数只有原稿的 {ratio:.0%}，可能删多了或做成了摘要")
        elif ratio < 0.80:
            add("提醒", "U7", f"字数为原稿的 {ratio:.0%}，偏低，自查是否删了实质内容")
        elif ratio > 0.97 and mode == "精修":
            add("提醒", "U8", f"字数为原稿的 {ratio:.0%}，几乎没变，确认是否真的整理过")
    for c in ctx.get("chunk_ratios", []):
        if c.get("kind") == "翻译":
            if c["ratio"] < 0.7:
                add("违规", "U9", f"第 {c['chunk']} 块译文汉字数只有英文词数的 {c['ratio']:.2f} 倍，可能漏译")
            continue
        if c["ratio"] < 0.65:
            add("违规", "U9", f"第 {c['chunk']} 块只有原文的 {c['ratio']:.0%}，可能被压缩")
        elif c["ratio"] > 1.02 and mode == "精修":
            add("提醒", "U9", f"第 {c['chunk']} 块比原文还长（{c['ratio']:.0%}），看是否混入了原文或重复")
    for k in ctx.get("seams", []):
        add("提醒", "U11", f"第 {k} 块在句中结束，读一下它与下一块的接缝，把断开的句子接好")
    if ctx.get("source_segments") and abs(len(body_paras) - ctx["source_segments"]) <= 2 and metrics.get("ratio", 0) > 0.95:
        add("提醒", "U10", "段落与原稿几乎一一对应、字数几乎没变：像是机械转换，不像整理过")

    # 标错人
    names_known = set(ctx.get("speaker_names") or [])
    bold_labels = []
    for no, p in body_paras:
        m = BOLD_NAME.match(p)
        if m:
            label = re.sub(r"[*：:\s]", "", m.group(0))
            bold_labels.append((no, label, p.split("\n")[0]))
    counts = {}
    for _, label, _ in bold_labels:
        counts[label] = counts.get(label, 0) + 1
    # 加粗的概念标签（“已知的已知：”）不算；像说话人的才算：已知人名、角色词、编号，或同一标签在段首反复出现
    bold_names = [(no, first) for no, label, first in bold_labels
                  if label in names_known or ROLE_WORD.fullmatch(label) or counts[label] >= 3]
    if bold_names:
        add("违规", "P1", f"人名加粗 {len(bold_names)} 处（人名一律不加粗）", bold_names[0][0], bold_names[0][1])
    ctype = ctx.get("content_type")
    mic = [(no, p) for no, p in body_paras if "🎙️" in p]
    if ctype in {"单人", "文章"} and mic:
        add("违规", "P2", f"单人内容不应出现 🎙️（{len(mic)} 处）", mic[0][0], mic[0][1])
    def mic_with_name(p):
        m = re.match(r"^🎙️\s*([^\s：]{1,12})：", p)
        return bool(m) and (m.group(1) in names_known or ROLE_WORD.fullmatch(m.group(1)) is not None)

    both = [(no, p) for no, p in body_paras if mic_with_name(p)]
    if both:
        add("违规", "P3", "同一段既有 🎙️ 又有名字", both[0][0], both[0][1])
    labeled = [(no, p) for no, p in body_paras
               if NUMBERED.match(p) or (LABEL_START.match(p) and LABEL_START.match(p).group(1) in names_known)]
    if ctx.get("has_labels") is False and ctx.get("speaker_mode") == "host_only":
        loose = [(no, p) for no, p in mic if "？" not in p and "?" not in p and not HOST_CUE.search(p)]
        if loose:
            add("提醒", "P6", f"有 {len(loose)} 段 🎙️ 既不是问句也不像串场，复核是否真是主持人的话，拿不准就去掉 🎙️", loose[0][0], loose[0][1])
    if ctx.get("has_labels") is False and ctx.get("speaker_mode") != "requested" and labeled:
        add("违规", "P4", f"原稿没有说话人标签，正文却标了 {len(labeled)} 处说话人（只允许给能确定的主持人提问加 🎙️）", labeled[0][0], labeled[0][1])
    if ctype in {"单人", "文章"} and labeled:
        add("违规", "P5", f"单人内容不应标说话人（{len(labeled)} 处）", labeled[0][0], labeled[0][1])

    # 格式
    for no, kind, ex in emphasis_problems(text):
        add("违规", "F1", f"强调标记{kind}", no, ex)
    for no, p in body_paras:
        if p.count("<u>") != p.count("</u>"):
            add("违规", "F2", "下划线标签不成对", no, p)
        if p.count("<small>") != p.count("</small>"):
            add("违规", "F2", "小字标签不成对", no, p)
        m = re.fullmatch(r"(?:🎙️\s*)?(?:[^：\n]{1,12}：)?<u>((?:(?!</?u>).)+)</u>[。！？]?", p.strip(), re.S)
        if m and len(m.group(1)) > 60:
            add("提醒", "F3", "整段画了下划线，只画关键句", no, p)
    n_u = body_text.count("<u>")
    metrics["underlines"] = n_u
    if zh_cjk > 2000:
        per_k = n_u * 1000 / zh_cjk
        metrics["underline_per_1k"] = round(per_k, 2)
        if per_k < 1.0:
            add("提醒", "F4", f"下划线偏少：每千字 {per_k:.1f} 处（目标约每 400–600 字一处）")
        elif per_k > 6:
            add("提醒", "F4", f"下划线过密：每千字 {per_k:.1f} 处")
    if bilingual:
        prose = [(no, p) for no, p in body_paras if not p.startswith("#")]
        for i, (no, p) in enumerate(prose):
            if p.startswith("<small>") or p.startswith("> <small>"):
                nxt = prose[i + 1][1] if i + 1 < len(prose) else ""
                if not nxt or "<small>" in nxt:
                    add("违规", "F5", "英文段后面缺中文译文", no, p)
    doubts = [(no, l.strip()) for no, l in enumerate(lines, 1) if "〔?〕" in l]
    metrics["doubts"] = len(doubts)
    if doubts:
        add("信息", "D1", f"存疑标记〔?〕共 {len(doubts)} 处", doubts[0][0], doubts[0][1])
    violations = [x for x in items if x["level"] == "违规"]
    return {"violations": len(violations), "warnings": len([x for x in items if x["level"] == "提醒"]),
            "items": items, "metrics": metrics}


def job_context(job_dir: Path) -> dict:
    stats = read_json(job_dir / "stats.json", {}) or {}
    sheet = read_json(job_dir / "sheet.json", {}) or {}
    ctx = {"job": True, "mode": sheet.get("mode"), "content_type": sheet.get("content_type"),
           "speaker_mode": sheet.get("speaker_mode"), "has_labels": stats.get("has_labels"),
           "speaker_names": [s.get("name") for s in sheet.get("speakers", []) if s.get("name")],
           "source_chars": stats.get("chars"), "source_cjk": stats.get("cjk") if stats.get("lang") == "zh" else None,
           "source_filler": stats.get("filler_per_1k"),
           "source_words": stats.get("words") if stats.get("lang") == "en" else None,
           "source_segments": stats.get("segments"), "chunk_ratios": []}
    tasks = read_json(job_dir / "tasks.json", []) or []
    ctx["seams"] = []
    for t in tasks[:-1]:
        out = job_dir / t["output"]
        if out.exists():
            tail = re.sub(r"</?u>|</?small>", "", out.read_text(encoding="utf-8").strip())
            if tail and not re.search(r"[。！？!?.…”」）)\]]$", tail):
                ctx["seams"].append(t["chunk"])
    for t in tasks:
        out = job_dir / t["output"]
        src = job_dir / t["input"]
        if out.exists() and src.exists() and t["kind"] != "翻译-整理":
            s_text = "".join(x["text"] for x in read_segments(src))
            o_text = strip_markup(re.sub(r"<small>.*?</small>", "", out.read_text(encoding="utf-8"), flags=re.S))
            s_cjk, o_cjk = count_cjk(s_text), count_cjk(o_text)
            if s_cjk > len(s_text) * 0.5:
                s_n, o_n = s_cjk, o_cjk
            else:
                s_n, o_n = len(re.sub(r"\s", "", s_text)), len(re.sub(r"\s", "", o_text))
            if t["kind"] == "精修" and s_n:
                ctx["chunk_ratios"].append({"chunk": t["chunk"], "ratio": round(o_n / s_n, 3)})
            elif t["kind"] == "翻译-精修" and not t.get("bilingual"):
                words = len(re.findall(r"[A-Za-z]+", s_text))
                if words:
                    ctx["chunk_ratios"].append({"chunk": t["chunk"], "kind": "翻译", "ratio": round(o_cjk / words, 3)})
    return ctx


def report(res: dict, title: str) -> None:
    print(f"[{title}] 违规 {res['violations']} · 提醒 {res['warnings']}")
    for x in res["items"]:
        loc = f"第 {x['line']} 行 " if x.get("line") else ""
        ex = f"：{x['excerpt']}" if x.get("excerpt") else ""
        print(f"  {x['level']} {x['code']} {loc}{x['message']}{ex}")
    m = res["metrics"]
    keys = [k for k in ("body_chars", "ratio", "filler_per_1k", "underlines", "underline_per_1k", "doubts") if k in m]
    print("  指标：" + " · ".join(f"{k}={m[k]}" for k in keys))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?")
    ap.add_argument("--job-dir")
    ap.add_argument("--source", help="原稿（source.md 或纯文本），用于算字数比例")
    ap.add_argument("--mode", choices=["整理", "精修", "翻译"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.job_dir:
        job = Path(a.job_dir).expanduser()
        path = job / "final.md"
        ctx = job_context(job)
    elif a.file:
        path = Path(a.file).expanduser()
        ctx = {"mode": a.mode}
        if a.source:
            src = Path(a.source).expanduser()
            segs = read_segments(src)
            raw = "\n".join(s["text"] for s in segs) if segs else src.read_text(encoding="utf-8", errors="ignore")
            ctx["source_chars"] = len(re.sub(r"\s", "", raw))
            ctx["source_cjk"] = count_cjk(raw) or None
            ctx["source_filler"] = filler_rate(raw)
    else:
        ap.error("给出成品文件或 --job-dir")
    if not path.exists():
        print(f"找不到 {path}", file=sys.stderr)
        return 2
    res = check(path.read_text(encoding="utf-8"), ctx)
    if a.job_dir:
        write_json(Path(a.job_dir).expanduser() / "check.json", res)
        update_status(Path(a.job_dir).expanduser(), checked=True, violations=res["violations"], warnings=res["warnings"])
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        report(res, path.name)
    return 1 if res["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
