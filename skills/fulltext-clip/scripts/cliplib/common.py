"""全文剪藏脚本的公共工具：任务目录、状态文件、网络请求、时间与文字处理、段落标记。

段落标记是整条流水线的约定。source.md、chunks/*.md 里每段以标记开头：

    〔s12 00:03:41 L1〕这一段的文字        语音类：段号 + 开始时间 + 说话人标签（都可缺省）
    〔p7〕这一段的文字                      文章类：段号

标签在准备阶段是平台原始标签的代号（L1、L2……）；apply-sheet 之后换成成品里的显示标签
（🎙️、名字、说话人1），没有标签的段落不写。标记只在任务目录里出现，成品里不能有。
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import re
import subprocess
import unicodedata
import urllib.request
import zlib
from pathlib import Path

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
BEIJING = dt.timezone(dt.timedelta(hours=8))

MARKER = re.compile(r"^〔([sp]\d+)(?: (\d{2}:\d{2}:\d{2}))?(?: ([^〕]+))?〕(.*)$", re.S)

# 状态码：准备脚本和拼装脚本都用这一套，分派说明按它决定下一步。
CODES = {
    "OK": "完成",
    "NEED_LOGIN": "平台要求登录",
    "NEED_UPGRADE": "yt-dlp 可能过旧",
    "BLOCKED": "平台拦截或签名校验失败",
    "NOT_FOUND": "内容不存在或已下架",
    "UNSUPPORTED": "没有可用的抓取途径",
    "PAID": "付费内容，拿不到音频或正文",
    "NO_TEXT": "取到页面但没有正文或字幕",
    "PARTIAL": "正文可能没抽全，需要指定起止段",
    "NEEDS_VISION": "需要看图（图片或扫描件）",
    "TRANSCRIBE_FAILED": "本机转写失败",
    "DUPLICATE": "输出目录已有同一内容",
    "CONFLICT": "输出目录已有同名文件",
    "FAILED": "其他失败",
}


# ---------- 文件与状态 ----------

def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def now_iso() -> str:
    return dt.datetime.now(BEIJING).isoformat(timespec="seconds")


def update_status(job_dir: Path, **fields) -> dict:
    path = Path(job_dir) / "status.json"
    status = read_json(path, {}) or {}
    status.update(fields)
    status["updated"] = now_iso()
    write_json(path, status)
    return status


# ---------- 网络与外部命令 ----------

def http_get(url: str, timeout: int = 30, headers: dict | None = None) -> tuple[str, str]:
    """返回 (最终 URL, 文本)。处理 gzip/deflate 与字符集。"""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            raw = zlib.decompress(raw)
        charset = resp.headers.get_content_charset()
        final = resp.geturl()
    if not charset:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4096], re.I)
        charset = m.group(1).decode() if m else "utf-8"
    try:
        return final, raw.decode(charset, errors="replace")
    except LookupError:
        return final, raw.decode("utf-8", errors="replace")


def run(cmd: list[str], timeout: int | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=str(cwd) if cwd else None
    )


def download(url: str, dest: Path, timeout: int = 1800) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = run(["curl", "-L", "--fail", "--retry", "3", "-sS", "-A", UA, "-o", str(dest), url], timeout=timeout)
    return r.returncode == 0 and dest.exists() and dest.stat().st_size > 0


# ---------- 时间 ----------

def hms(seconds: float | int | None) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_hms(value: str) -> float:
    """解析 1:02:03、01:02、00:01:02,345、00:01:02.345。"""
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def beijing_date(value) -> str | None:
    """ISO 时间、Unix 时间戳或 YYYYMMDD → 北京时间的 YYYY-MM-DD。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit() and len(value) >= 10):
        ts = float(value)
        if ts > 1e12:  # 毫秒
            ts /= 1000
        return dt.datetime.fromtimestamp(ts, BEIJING).date().isoformat()
    value = str(value).strip()
    if re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            d = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            m = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", value)
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
    if d.tzinfo is None:
        return d.date().isoformat()
    return d.astimezone(BEIJING).date().isoformat()


def minutes_text(seconds) -> str | None:
    if not seconds:
        return None
    m = round(float(seconds) / 60)
    return f"{m} 分钟" if m < 120 else f"{m} 分钟（{m // 60} 小时 {m % 60} 分）"


# ---------- 文字 ----------

CJK = re.compile(r"[㐀-鿿豈-﫿]")
LATIN_WORD = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")


def count_cjk(text: str) -> int:
    return len(CJK.findall(text))


def body_size(text: str) -> dict:
    """中文按字、英文按词计数；忽略空白和标记。"""
    plain = strip_markup(text)
    return {"cjk": count_cjk(plain), "words": len(LATIN_WORD.findall(plain)), "chars": len(re.sub(r"\s", "", plain))}


def lang_of(text: str) -> str:
    size = body_size(text[:20000])
    if size["cjk"] >= size["words"] * 0.6:
        return "zh"
    return "en" if size["words"] else "zh"


def strip_markup(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"〔[sp]\d+[^〕]*〕", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`#>|]", "", text)
    return text


def sanitize_filename(name: str, limit: int = 120) -> str:
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[/\\:*?"<>|\r\n\t]', " ", name)
    name = re.sub(r"\s{2,}", " ", name).strip(" .")
    return name[:limit].rstrip(" .") or "未命名"


def parse_marker(line: str):
    """返回 (段号, 时间, 标签, 文字) 或 None。"""
    m = MARKER.match(line)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4).strip()


def make_marker(seg_id: str, ts: str | None = None, label: str | None = None) -> str:
    parts = [seg_id]
    if ts:
        parts.append(ts)
    if label:
        parts.append(label)
    return "〔" + " ".join(parts) + "〕"


def read_segments(path: Path) -> list[dict]:
    """读 source.md 或块文件里的段落（跳过块文件的说明头和上文衔接区）。"""
    segs = []
    in_context = False
    for block in Path(path).read_text(encoding="utf-8").split("\n\n"):
        block = block.strip("\n")
        if block.startswith("〔上文末尾"):
            in_context = True
            continue
        if block.startswith("〔本块开始〕"):
            in_context = False
            block = block[len("〔本块开始〕"):].strip("\n")
            if not block:
                continue
        if in_context:
            continue
        parsed = parse_marker(block)
        if parsed:
            seg_id, ts, label, text = parsed
            segs.append({"id": seg_id, "ts": ts, "label": label, "text": text})
    return segs


def write_segments(path: Path, segs: list[dict], header: str = "") -> None:
    lines = [header.rstrip("\n")] if header else []
    for s in segs:
        lines.append(make_marker(s["id"], s.get("ts"), s.get("label")) + s["text"])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def load_paths_md(project_root: Path | None = None) -> dict:
    """从 .local/paths.md 的表格里读 `变量` | `值`。找不到返回空字典。"""
    root = Path(project_root) if project_root else Path.cwd()
    for base in [root, *root.parents]:
        p = base / ".local" / "paths.md"
        if p.exists():
            out = {}
            for m in re.finditer(r"^\|\s*`([A-Z_]+)`\s*\|\s*`([^`]+)`\s*\|", p.read_text(encoding="utf-8"), re.M):
                out[m.group(1)] = os.path.expanduser(m.group(2))
            return out
    return {}
