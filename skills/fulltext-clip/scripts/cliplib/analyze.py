"""统计、建议档位、切块、查重。

统计给模型当证据用（谁说得多、噪音多不多、有哪些疑似识别错误的英文小写词），
档位只是建议，由查询代理在工作单里确认。
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from pathlib import Path

from .common import count_cjk, parse_hms, strip_markup

FILLERS_ZH = ["嗯", "呃", "额，", "啊，", "就是说", "然后然后", "对对对", "这样的一个", "那个那个", "就是就是", "的话的话"]
# 中文正文里常见、不算识别错误的小写英文
LOWER_OK = {
    "app", "apps", "vs", "ok", "etc", "http", "https", "www", "com", "cn", "org", "pdf", "ppt", "wifi", "blog",
    "vlog", "logo", "slogan", "email", "iphone", "ipad", "macbook", "ios", "macos", "github", "prompt", "prompts",
    "token", "tokens", "agent", "agents", "skill", "skills", "vibe", "coding", "workflow", "the", "of", "and",
    "to", "in", "is", "for", "on", "with", "a", "an", "or", "by", "at", "as", "be", "it", "we", "you", "me",
}
SENT_PUNCT = re.compile(r"[。！？；，、,.!?;:：]")


def filler_rate(text: str) -> float:
    n = sum(text.count(f) for f in FILLERS_ZH)
    size = max(1, count_cjk(text))
    return round(n * 1000 / size, 2)


def lower_latin_tokens(text: str) -> Counter:
    """中文语境里的小写英文词（常是识别错误，如 icl、mb ti、gtv）。英文句子里的词不算。"""
    found = Counter()
    for m in re.finditer(r"(?<![A-Za-z])([a-z]{2,8})(?![A-Za-z])", text):
        word = m.group(1)
        if word in LOWER_OK:
            continue
        window = text[max(0, m.start() - 6):m.end() + 6]
        if count_cjk(window) >= 3:
            found[word] += 1
    return found


def compute_stats(items: list[dict], kind: str, lang: str, labels: dict) -> dict:
    texts = [x["text"] for x in items]
    joined = "\n".join(texts)
    plain = strip_markup(joined)
    chars = len(re.sub(r"\s", "", plain))
    punct = len(SENT_PUNCT.findall(plain))
    stats = {
        "kind": kind,
        "lang": lang,
        "chars": chars,
        "cjk": count_cjk(plain),
        "words": len(re.findall(r"[A-Za-z]+", plain)),
        "segments": len(items),
        "punct_per_100": round(punct * 100 / max(1, chars), 2),
        "has_timestamps": any(x.get("ts") for x in items),
        "has_labels": any(x.get("label") and x.get("label") != "换人" for x in items),
        "turn_marks": sum(1 for x in items if x.get("turn")),
    }
    if lang == "en":
        words = max(1, stats["words"])
        fill = len(re.findall(r"\b(?:um+|uh+|you know|I mean)\b", plain, re.I))
        repeats = len(re.findall(r"\b(\w+)\s+\1\b", plain, re.I))
        stats["en_filler_per_1k_words"] = round((fill + repeats) * 1000 / words, 2)
    if lang == "zh":
        stats["filler_per_1k"] = filler_rate(plain)
        stats["lower_latin"] = dict(lower_latin_tokens(plain).most_common(30))
    if stats["has_labels"]:
        per = {}
        for x in items:
            lab = x.get("label")
            if not lab or lab == "换人":
                continue
            d = per.setdefault(lab, {"raw": labels.get(lab, lab), "segments": 0, "chars": 0, "first_lines": []})
            d["segments"] += 1
            d["chars"] += len(x["text"])
            if len(d["first_lines"]) < 3:
                d["first_lines"].append(x["text"][:80])
        total = sum(d["chars"] for d in per.values()) or 1
        for d in per.values():
            d["share"] = round(d["chars"] / total, 3)
        stats["labels"] = per
    stats["suggested_mode"], stats["en_clean"] = suggest_mode(stats)
    return stats


def coverage(stats: dict, duration_s) -> dict | None:
    """按时长估算应有的文字量：中文每分钟 200–280 字，英文每分钟 130–170 词。低于下限的 60% 视为节选或截断。"""
    if not duration_s or stats.get("kind") not in {"asr", "subs_manual", "subs_auto", "platform_transcript"}:
        return None
    minutes = float(duration_s) / 60
    if stats["lang"] == "zh":
        actual, low = stats["cjk"], 200 * minutes
    else:
        actual, low = stats["words"], 130 * minutes
    ratio = actual / low if low else None
    return {"expected_min": int(low), "actual": actual, "ratio": round(ratio, 2) if ratio else None,
            "suspect": bool(ratio is not None and ratio < 0.6)}


def suggest_mode(stats: dict) -> tuple[str, bool]:
    kind = stats["kind"]
    if kind == "images":
        return "看图", False
    if stats["lang"] == "en":
        clean = (kind in {"article", "document", "subs_manual"} and stats["punct_per_100"] >= 1.5
                 and stats.get("en_filler_per_1k_words", 0) < 8)
        return "翻译", clean
    if kind in {"article", "document"} and stats["punct_per_100"] >= 3 and stats.get("filler_per_1k", 0) < 2:
        return "整理", False
    return "精修", False


# ---------- 切块 ----------

def _ts_seconds(ts):
    return parse_hms(ts) if ts else None


def plan_chunks(items: list[dict], target: int, chapters: list[dict] | None = None) -> list[tuple[int, int]]:
    """返回 [(起, 止)] 下标区间（含起不含止）。优先在章节边界、换人处、长停顿、句末处切。"""
    if not items:
        return []
    sizes = [len(x["text"]) for x in items]
    total = sum(sizes)
    if total <= target * 1.25:
        return [(0, len(items))]
    chapter_starts = sorted(c["start_s"] for c in (chapters or []))

    def boundary_score(i: int) -> float:
        """在 items[i-1] 与 items[i] 之间切的得分。"""
        a, b = items[i - 1], items[i]
        score = 0.0
        tb, ta = _ts_seconds(b.get("ts")), _ts_seconds(a.get("ts"))
        if tb is not None and any(abs(tb - c) <= 20 for c in chapter_starts):
            score += 3
        if b.get("label") and b.get("label") != a.get("label"):
            score += 2
        if b.get("turn"):
            score += 1.5
        if b["text"].startswith("#"):
            score += 3
        if re.search(r"[。！？!?.]$", a["text"]):
            score += 0.5
        return score

    n = len(items)
    chunks: list[tuple[int, int]] = []
    start = 0
    while start < n:
        if sum(sizes[start:]) <= target * 1.25:
            chunks.append((start, n))
            break
        best, best_score, acc = None, float("-inf"), 0
        for j in range(start + 1, n):
            acc += sizes[j - 1]  # items[start:j] 的字数
            if acc < target * 0.8:
                continue
            if acc > target * 1.25:
                break
            sc = boundary_score(j) - abs(acc - target) / target
            if sc > best_score:
                best, best_score = j, sc
        if best is None:  # 单段就超长，或窗口里没有可切点
            acc = 0
            for j in range(start + 1, n + 1):
                acc += sizes[j - 1]
                if acc >= target:
                    best = j
                    break
            best = best or n
        chunks.append((start, best))
        start = best
    if len(chunks) >= 2 and sum(sizes[chunks[-1][0]:chunks[-1][1]]) < target * 0.3:
        last = chunks.pop()
        chunks[-1] = (chunks[-1][0], last[1])
    return chunks


# ---------- 查重与相关旧笔记 ----------

def _norm_title(s: str) -> str:
    s = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", s)
    s = re.sub(r"[\s\W_]+", "", s.lower())
    return s


def find_related(output_dir: Path | None, meta: dict, limit: int = 5) -> dict:
    out = {"duplicates": [], "similar": []}
    if not output_dir or not Path(output_dir).is_dir():
        return out
    files = list(Path(output_dir).glob("*.md")) + list(Path(output_dir).glob("*/*.md"))
    sid = meta.get("stable_id")
    url = (meta.get("url") or "").split("#")[0].rstrip("/")
    title_n = _norm_title(meta.get("title") or "")
    scored = []
    for f in files:
        try:
            head = f.read_text(encoding="utf-8", errors="ignore")[:6000]
        except OSError:
            continue
        if (sid and len(str(sid)) >= 8 and str(sid) in head) or (url and len(url) > 25 and url in head):
            out["duplicates"].append(str(f))
            continue
        if title_n:
            ratio = difflib.SequenceMatcher(None, title_n, _norm_title(f.stem)).ratio()
            if ratio >= 0.55:
                scored.append((ratio, str(f)))
    out["similar"] = [p for _, p in sorted(scored, reverse=True)[:limit]]
    return out
