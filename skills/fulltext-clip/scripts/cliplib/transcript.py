"""各种文字来源 → 统一的段落列表。

段落是字典：{"ts": "00:03:41" 或 None, "label": 原始说话人标签或 None, "text": 文字, "turn": 是否换人线索}。
这里只做确定性的整理：拆行、去时间码、去滚动字幕的重复、按停顿合并短句。
断句、纠错、认人都留给模型。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .common import hms, parse_hms, run

# ---------- 字幕（SRT / VTT） ----------

_TIME = r"(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}"
CUE_TIME = re.compile(rf"^\s*({_TIME})\s*-->\s*({_TIME})")
TAG = re.compile(r"<[^>]+>")


def parse_subtitles(text: str) -> list[dict]:
    """SRT 与 VTT 都认。返回 [{"start": 秒, "end": 秒, "text": 文字}]。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    cues = []
    for block in re.split(r"\n\s*\n", text):
        lines = [l for l in block.split("\n") if l.strip()]
        idx = next((i for i, l in enumerate(lines) if CUE_TIME.match(l)), None)
        if idx is None:
            continue
        m = CUE_TIME.match(lines[idx])
        body = [TAG.sub("", l).replace("\\h", " ").replace("\\N", " ").strip() for l in lines[idx + 1:]]
        body = [re.sub(r"\s{2,}", " ", re.sub(r"&nbsp;|\u00a0", " ", l)).strip() for l in body if l]
        if body:
            cues.append({"start": parse_hms(m.group(1)), "end": parse_hms(m.group(3)), "lines": body})
    # 滚动字幕：后一条的第一行常常重复前一条的最后一行，逐行去重
    out = []
    last_line = None
    for c in cues:
        new_lines = []
        for line in c["lines"]:
            if line == last_line:
                continue
            new_lines.append(line)
            last_line = line
        if new_lines:
            out.append({"start": c["start"], "end": c["end"], "text": " ".join(new_lines)})
    return out


def cues_to_segments(cues: list[dict], lang: str, gap: float = 1.5, max_len: int | None = None) -> list[dict]:
    """把字幕短句按停顿、句末标点和长度合并成段。“>>”当作换人线索。"""
    max_len = max_len or (220 if lang == "zh" else 600)
    joiner = "" if lang == "zh" else " "
    segs: list[dict] = []
    cur = None
    prev_end = None
    for c in cues:
        text = c["text"].strip()
        turn = False
        if text.startswith(">>"):
            turn = True
            text = text.lstrip("> ").strip()
        if text.startswith("- ") and lang != "zh":
            turn = True
            text = text[2:]
        if not text:
            continue
        pause = prev_end is not None and c["start"] - prev_end >= gap
        ended = bool(cur) and re.search(r"[。！？!?.…]$", cur["text"]) is not None
        too_long = bool(cur) and len(cur["text"]) >= max_len
        if cur is None or turn or too_long or (pause and (ended or len(cur["text"]) > max_len * 0.3)):
            cur = {"ts": hms(c["start"]), "label": None, "text": text, "turn": turn}
            segs.append(cur)
        else:
            cur["text"] = cur["text"] + joiner + text
        prev_end = c["end"]
    return segs


# ---------- 平台文字稿（带说话人或时间码的逐行文本） ----------

WIDE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")  # 中文字与全角标点，拼行时不加空格

LABEL_PATTERNS = [
    # 百度听记、小宇宙导出：“1号讲话人  00:00:06文字”
    re.compile(r"^(?P<label>\d+号讲话人)\s+(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\s*(?P<text>.*)$"),
    # “Speaker 1 00:00:05 文字”“说话人 2 01:02”
    re.compile(r"^(?P<label>(?:Speaker|说话人|发言人|讲话人|发言者)\s*\d+)[\s:：]+(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\s*(?P<text>.*)$", re.I),
    # 飞书妙记：“张三 00:01:02”单独一行，文字在下一行
    re.compile(r"^(?P<label>[^\s\d:：][^\s:：]{0,19})\s+(?P<ts>\d{1,2}:\d{2}:\d{2})\s*$"),
    # 只有时间码：“[00:01:02] 文字”“00:01 文字”
    re.compile(r"^\[?(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]?\s+(?P<text>.+)$"),
]


def parse_labeled_lines(lines: list[str]) -> tuple[list[dict], dict]:
    """识别平台文字稿。返回 (段落, 信息)；信息里 matched 为命中行比例，title 为开头的标题行。"""
    segs: list[dict] = []
    hits = 0
    total = 0
    title = None
    for raw in lines:
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        total += 1
        m = None
        for pat in LABEL_PATTERNS:
            m = pat.match(line)
            if m:
                break
        if m:
            hits += 1
            gd = m.groupdict()
            ts = gd.get("ts")
            ts = hms(parse_hms(ts)) if ts else None
            segs.append({"ts": ts, "label": gd.get("label"), "text": (gd.get("text") or "").strip(), "turn": False})
        elif segs:
            prev = segs[-1]["text"]
            glue = "" if (not prev or WIDE.match(prev[-1]) or WIDE.match(line[0])) else " "
            segs[-1]["text"] = (prev + glue + line).strip()
        elif title is None:
            title = line
    segs = [s for s in segs if s["text"]]
    return segs, {"matched": hits / total if total else 0.0, "title": title}


def normalize_labels(segs: list[dict]) -> dict:
    """把原始标签换成 L1、L2……（按首次出现顺序），返回 {代号: 原始标签}。"""
    mapping: dict[str, str] = {}
    reverse: dict[str, str] = {}
    for s in segs:
        raw = s.get("label")
        if not raw:
            continue
        if raw not in reverse:
            code = f"L{len(reverse) + 1}"
            reverse[raw] = code
            mapping[code] = raw
        s["label"] = reverse[raw]
    return mapping


def merge_same_speaker(segs: list[dict], lang: str, max_len: int | None = None) -> list[dict]:
    """同一说话人的相邻短段合并，控制段长，便于阅读和切块。"""
    max_len = max_len or (400 if lang == "zh" else 900)
    joiner = "" if lang == "zh" else " "
    out: list[dict] = []
    for s in segs:
        if out and s.get("label") == out[-1].get("label") and not s.get("turn") \
                and len(out[-1]["text"]) + len(s["text"]) <= max_len:
            out[-1]["text"] += joiner + s["text"]
        else:
            out.append(dict(s))
    return out


# ---------- 文档 ----------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def docx_paragraphs(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(f"{_W}body")
    out = []
    for p in body.iter(f"{_W}p"):
        out.append("".join(t.text or "" for t in p.iter(f"{_W}t")))
    return out


def pptx_slides(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        names = sorted(
            (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),
        )
        slides = []
        for n in names:
            root = ET.fromstring(z.read(n))
            lines = ["".join(t.text or "" for t in p.iter(f"{_A}t")).strip() for p in root.iter(f"{_A}p")]
            slides.append("\n".join(l for l in lines if l))
    return slides


def pdf_text(path: Path) -> tuple[list[str], dict]:
    """pdftotext 抽文字并修断行。返回 (段落, 信息)。每页少于 50 字视为扫描件。"""
    r = run(["pdftotext", "-enc", "UTF-8", str(path), "-"], timeout=300)
    if r.returncode != 0:
        return [], {"error": r.stderr.strip()[:200]}
    pages = r.stdout.split("\f")
    info_r = run(["pdfinfo", str(path)], timeout=60)
    info = {}
    for line in info_r.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    n_pages = max(1, len([p for p in pages if p.strip()]) or len(pages))
    chars = len(re.sub(r"\s", "", r.stdout))
    info["scanned"] = chars / n_pages < 50
    paras = []
    for page in pages:
        buf = ""
        for line in page.split("\n"):
            line = line.strip()
            if not line:
                if buf:
                    paras.append(buf)
                    buf = ""
                continue
            if not buf:
                buf = line
            elif buf.endswith("-") and re.match(r"[a-z]", line):
                buf = buf[:-1] + line
            elif re.search(r"[一-鿿，、（]$", buf) and re.match(r"[一-鿿]", line):
                buf += line
            else:
                buf += " " + line
        if buf:
            paras.append(buf)
    return paras, info


def plain_paragraphs(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").lstrip("\ufeff")
    if "\n\n" in text.strip():
        parts = re.split(r"\n\s*\n", text)
    else:
        parts = text.split("\n")
    return [re.sub(r"[ \t]+", " ", p).strip() for p in parts if p.strip()]
