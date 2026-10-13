#!/usr/bin/env python3
"""全文剪藏 · 准备脚本（不花模型额度）。

用法（在 skill 根目录运行）:
  python3 scripts/prep.py init --input <链接或文件> --job-dir <任务目录> [--output-dir <成品目录>]
         [--language auto|zh|en] [--chunk-chars N] [--no-transcribe]
  python3 scripts/prep.py apply-sheet --job-dir <任务目录>      # 查询代理写完 sheet.json 之后
  python3 scripts/prep.py select --job-dir <任务目录> --from p12 --to p87   # 网页正文抽不准时
  python3 scripts/prep.py status <任务目录或批次目录> [...]

init 产出：meta.json（元信息）、source.md（带段落标记的全文）、stats.json（统计与建议档位）、
chunks/NNN.md（写作块）、status.json（阶段与状态码）。最后一行打印一行 JSON 摘要。
路径默认从 .local/paths.md 读取（OUTPUT_DIR、WHISPER_MODEL、FIREFOX_YTDLP_PROFILE、YTDLP_COOKIES_FILE）。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cliplib.analyze import compute_stats, coverage, find_related, plan_chunks  # noqa: E402
from cliplib.common import (  # noqa: E402
    CODES, lang_of, load_paths_md, read_json, read_segments, run, update_status, write_json, write_segments,
)
from cliplib.platforms import acquire  # noqa: E402
from cliplib.transcript import cues_to_segments, merge_same_speaker, normalize_labels, parse_subtitles  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
CHUNK_DEFAULT = {"zh": 10000, "en": 8000}
CONTENT_TYPES = {"单人", "采访", "对谈", "讨论", "文章"}
MODES = {"整理", "精修", "翻译"}


def config(args) -> dict:
    paths = load_paths_md(Path.cwd())
    if not paths:
        paths = load_paths_md(SCRIPT_DIR)
    return {
        "output_dir": args.output_dir or paths.get("OUTPUT_DIR"),
        "whisper_model": getattr(args, "whisper_model", None) or os.environ.get("WHISPER_MODEL") or paths.get("WHISPER_MODEL"),
        "cookies_profile": getattr(args, "cookies_profile", None) or paths.get("FIREFOX_YTDLP_PROFILE"),
        "cookies_file": getattr(args, "cookies_file", None) or paths.get("YTDLP_COOKIES_FILE"),
    }


# ---------- 转写 ----------

def whisper_prompt(meta: dict, lang: str) -> str:
    """给转写的提示词：标题、主播和时间轴话题词最能帮 whisper 写对专名；没有时间轴才用简介开头。"""
    topics = "、".join(c["title"] for c in meta.get("chapters") or [])
    desc = re.sub(r"https?://\S+|\d{1,2}:\d{2}(:\d{2})?", " ", meta.get("description") or "")
    desc = re.sub(r"\s+", " ", desc).strip()
    extra = topics or desc
    if lang == "zh":
        show = f"播客《{meta['podcast']}》" if meta.get("podcast") else ""
        host = f"，主播{meta['author']}" if meta.get("author") else ""
        return f"以下是{show}《{meta.get('title', '')}》{host}的简体中文逐字稿，有标点。{extra}"[:160]
    return (f"{meta.get('title', '')}. {meta.get('author') or ''}. " + extra)[:300]


def transcribe(audio: str, job_dir: Path, lang: str | None, meta: dict, cfg: dict) -> tuple[list[dict], str]:
    lock_path = Path.home() / ".cache" / "fulltext-clip" / "whisper.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    language = lang or "auto"
    cmd = ["bash", str(SCRIPT_DIR / "transcribe_long_media.sh"), "--input", audio, "--job-dir", str(job_dir / "asr"),
           "--language", language, "--prompt", whisper_prompt(meta, lang or "zh"), "--jobs", "2"]
    if cfg.get("whisper_model"):
        cmd += ["--model", os.path.expanduser(cfg["whisper_model"])]
    update_status(job_dir, stage="transcribing")
    with open(lock_path, "w") as lock:  # 本机一次只转一个
        fcntl.flock(lock, fcntl.LOCK_EX)
        r = run(cmd)
    srt = job_dir / "asr" / "transcript.srt"
    if r.returncode != 0 or not srt.exists():
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        return [], lines[-1] if lines else "转写失败"
    text = srt.read_text(encoding="utf-8", errors="replace")
    real_lang = lang or lang_of(text)
    return cues_to_segments(parse_subtitles(text), real_lang, gap=1.0), ""


# ---------- 块文件 ----------

def chunk_header(k: int, total: int, seg_slice: list[dict], mode_hint: str) -> str:
    first, last = seg_slice[0], seg_slice[-1]
    span = f"{first['id']}–{last['id']}"
    ts = f" · {first['ts']}–{last['ts']}" if first.get("ts") and last.get("ts") else ""
    chars = sum(len(s["text"]) for s in seg_slice)
    return f"〔块说明〕第 {k}/{total} 块 · {span}{ts} · 约 {chars:,} 字 · 建议档位：{mode_hint}"


def write_chunks(job_dir: Path, segs: list[dict], plan: list[tuple[int, int]], mode_hint: str) -> None:
    cdir = job_dir / "chunks"
    cdir.mkdir(parents=True, exist_ok=True)
    for old in cdir.glob("*.md"):
        old.unlink()
    for k, (a, b) in enumerate(plan, 1):
        header = chunk_header(k, len(plan), segs[a:b], mode_hint)
        if a > 0:
            tail = "".join(s["text"] for s in segs[max(0, a - 3):a])[-300:].replace("\n", " ")
            header += f"\n\n〔上文末尾，仅供衔接，不要输出〕\n\n{tail}\n\n〔本块开始〕"
        write_segments(cdir / f"{k:03d}.md", segs[a:b], header)


# ---------- 命令 ----------

def cmd_init(args) -> int:
    cfg = config(args)
    job_dir = Path(args.job_dir).expanduser()
    (job_dir / "raw").mkdir(parents=True, exist_ok=True)
    update_status(job_dir, stage="acquiring", input=args.input, code=None, message="")
    res = acquire(args.input, job_dir, cfg)
    meta = res["meta"]
    code = res["code"]
    if code not in {"OK", "PARTIAL"}:
        write_json(job_dir / "meta.json", meta)
        st = update_status(job_dir, stage="failed" if code != "NEEDS_VISION" else "needs_vision",
                           code=code, message=res["message"] or CODES.get(code, ""), kind=res.get("kind"))
        print(json.dumps({"job_dir": str(job_dir), **{k: st.get(k) for k in ("stage", "code", "message")}}, ensure_ascii=False))
        return 0 if code == "NEEDS_VISION" else 2
    lang = args.language if args.language in {"zh", "en"} else res.get("lang")
    items = res["segments"]
    kind = res["kind"]
    if res.get("audio") and not items:
        if args.no_transcribe:
            write_json(job_dir / "meta.json", meta)
            update_status(job_dir, stage="needs_transcribe", code="OK", message="音频已就绪，未转写", audio=res["audio"])
            print(json.dumps({"job_dir": str(job_dir), "stage": "needs_transcribe", "audio": res["audio"]}, ensure_ascii=False))
            return 0
        items, err = transcribe(res["audio"], job_dir, lang, meta, cfg)
        if not items:
            write_json(job_dir / "meta.json", meta)
            update_status(job_dir, stage="failed", code="TRANSCRIBE_FAILED", message=err)
            print(json.dumps({"job_dir": str(job_dir), "stage": "failed", "code": "TRANSCRIBE_FAILED", "message": err}, ensure_ascii=False))
            return 2
    if items:
        lang = lang or lang_of(" ".join(x["text"] for x in items[:200]))
        labels = normalize_labels(items)
        if labels:
            items = merge_same_speaker(items, lang)
        segs = [{"id": f"s{i}", "ts": x.get("ts"), "label": x.get("label"), "text": x["text"], "turn": x.get("turn")}
                for i, x in enumerate(items, 1)]
        for s in segs:
            if s.get("turn") and not s.get("label"):
                s["label"] = "换人"
    else:
        blocks = [b for b in res["blocks"] if b.strip() != "〔图〕"]
        if len(blocks) < len(res["blocks"]):
            meta["images_skipped"] = len(res["blocks"]) - len(blocks)
        lang = lang or lang_of("\n".join(blocks[:200]))
        labels = {}
        segs = [{"id": f"p{i}", "ts": None, "label": None, "text": b} for i, b in enumerate(blocks, 1)]
    meta["lang"] = lang
    meta["labels"] = labels
    write_segments(job_dir / "source.md", segs)
    stats = compute_stats(segs, kind, lang, labels)
    stats["coverage"] = coverage(stats, meta.get("duration_s"))
    stats["related"] = find_related(cfg.get("output_dir"), meta)
    target = args.chunk_chars or CHUNK_DEFAULT.get(lang, 10000)
    plan = plan_chunks(segs, target, meta.get("chapters"))
    stats["chunk_plan"] = [{"chunk": f"{k:03d}", "from": segs[a]["id"], "to": segs[b - 1]["id"],
                            "chars": sum(len(s["text"]) for s in segs[a:b])} for k, (a, b) in enumerate(plan, 1)]
    write_chunks(job_dir, segs, plan, stats["suggested_mode"])
    write_json(job_dir / "meta.json", meta)
    write_json(job_dir / "stats.json", stats)
    dup = stats["related"]["duplicates"]
    message = ("输出目录已有同一内容：" + "；".join(Path(d).name for d in dup)) if dup else res["message"]
    cov = stats.get("coverage")
    if cov and cov["suspect"]:
        message = (message + "；" if message else "") + f"文字量只有按时长估算下限的 {cov['ratio']:.0%}，可能是节选或截断"
    st = update_status(job_dir, stage="prepared", code="DUPLICATE" if dup else code, message=message,
                       kind=kind, lang=lang, mode_suggested=stats["suggested_mode"], chunks=len(plan),
                       chars=stats["chars"], has_labels=stats["has_labels"])
    print(json.dumps({"job_dir": str(job_dir), "stage": st["stage"], "code": st["code"], "title": meta.get("title"),
                      "kind": kind, "lang": lang, "chars": stats["chars"], "chunks": len(plan),
                      "mode": stats["suggested_mode"], "has_labels": stats["has_labels"], "message": st["message"]},
                     ensure_ascii=False))
    return 0


def display_labels(sheet: dict, stats: dict) -> dict:
    ctype = sheet["content_type"]
    codes = list((stats.get("labels") or {}).keys())
    speakers = {s["label"]: s for s in sheet.get("speakers", []) if s.get("label")}
    if ctype in {"单人", "文章"} or sheet.get("speaker_mode") == "none":
        return {c: None for c in codes}
    guests = [c for c in codes if speakers.get(c, {}).get("role") != "host"]
    guest_names = {speakers.get(c, {}).get("name") for c in guests}
    out, n = {}, 0
    numbered: dict[str, str] = {}
    for c in codes:
        sp = speakers.get(c, {})
        if ctype == "采访" and sp.get("role") == "host":
            out[c] = "🎙️"
        elif ctype == "采访" and len(guest_names) <= 1:
            out[c] = None
        elif sp.get("name") and sp.get("confirmed"):
            out[c] = sp["name"]
        else:
            key = sp.get("name") or c
            if key not in numbered:
                n += 1
                numbered[key] = f"说话人{n}"
            out[c] = numbered[key]
    return out


def cmd_apply_sheet(args) -> int:
    job_dir = Path(args.job_dir).expanduser()
    sheet = read_json(job_dir / "sheet.json")
    stats = read_json(job_dir / "stats.json")
    if not sheet or not stats:
        print("缺 sheet.json 或 stats.json", file=sys.stderr)
        return 2
    problems = []
    if sheet.get("content_type") not in CONTENT_TYPES:
        problems.append(f"content_type 应为 {sorted(CONTENT_TYPES)}")
    if sheet.get("mode") not in MODES:
        problems.append(f"mode 应为 {sorted(MODES)}")
    if sheet.get("speaker_mode") not in {"none", "host_only", "labels", "requested"}:
        problems.append("speaker_mode 应为 none/host_only/labels/requested")
    for key in ("title", "title_zh", "published", "source_line"):
        if key not in sheet:
            problems.append(f"缺字段 {key}（没有就写空字符串）")
    if problems:
        print("sheet.json 有问题：\n- " + "\n- ".join(problems), file=sys.stderr)
        return 2
    mode = sheet["mode"]
    chunk_files = sorted((job_dir / "chunks").glob("*.md"))
    if stats.get("has_labels"):
        mapping = display_labels(sheet, stats)
        for f in chunk_files:
            head_parts = []
            for b in f.read_text(encoding="utf-8").split("\n\n"):
                if b.startswith("〔s") or b.startswith("〔p"):
                    break
                head_parts.append(b)
            segs = read_segments(f)
            for s in segs:
                if s.get("label") in mapping:
                    s["label"] = mapping[s["label"]]
            write_segments(f, segs, "\n\n".join(head_parts))
        sheet_labels = mapping
    else:
        sheet_labels = {}
    tasks = []
    out_dir = job_dir / "out"
    out_dir.mkdir(exist_ok=True)
    if mode == "整理":
        body = [s["text"] for s in read_segments(job_dir / "source.md")]
        (out_dir / "001.md").write_text("\n\n".join(body) + "\n", encoding="utf-8")
    else:
        en_clean = mode == "翻译" and stats.get("en_clean") and not sheet.get("en_needs_cleanup")
        for f in chunk_files:
            k = f.stem
            kind = "翻译-整理" if en_clean else ("翻译-精修" if mode == "翻译" else "精修")
            task = {"chunk": k, "input": f"chunks/{k}.md", "output": f"out/{k}.zh.md" if en_clean else f"out/{k}.md",
                    "notes": f"notes/{k}.json", "kind": kind}
            if mode == "翻译":
                task["bilingual"] = bool(sheet.get("bilingual"))  # 默认只出中文，用户要求双语才配英文
            tasks.append(task)
    (job_dir / "notes").mkdir(exist_ok=True)
    write_json(job_dir / "tasks.json", tasks)
    update_status(job_dir, stage="surveyed", mode=mode, content_type=sheet["content_type"],
                  speaker_mode=sheet["speaker_mode"], display_labels=sheet_labels, tasks=len(tasks))
    print(json.dumps({"job_dir": str(job_dir), "stage": "surveyed", "mode": mode, "tasks": [t["chunk"] for t in tasks],
                      "display_labels": sheet_labels}, ensure_ascii=False))
    return 0


def cmd_select(args) -> int:
    job_dir = Path(args.job_dir).expanduser()
    segs = read_segments(job_dir / "source.md")
    ids = [s["id"] for s in segs]
    if args.frm not in ids or args.to not in ids:
        print(f"段号不存在：{args.frm} / {args.to}", file=sys.stderr)
        return 2
    a, b = ids.index(args.frm), ids.index(args.to) + 1
    (job_dir / "raw" / "source_full.md").write_text((job_dir / "source.md").read_text(encoding="utf-8"), encoding="utf-8")
    segs = [{**s, "id": f"p{i}"} for i, s in enumerate(segs[a:b], 1)]
    meta = read_json(job_dir / "meta.json", {})
    old = read_json(job_dir / "stats.json", {})
    write_segments(job_dir / "source.md", segs)
    stats = compute_stats(segs, old.get("kind", "article"), meta.get("lang", "zh"), {})
    stats["related"] = old.get("related", {})
    plan = plan_chunks(segs, CHUNK_DEFAULT.get(meta.get("lang"), 10000))
    stats["chunk_plan"] = [{"chunk": f"{k:03d}", "from": segs[x]["id"], "to": segs[y - 1]["id"],
                            "chars": sum(len(s["text"]) for s in segs[x:y])} for k, (x, y) in enumerate(plan, 1)]
    write_chunks(job_dir, segs, plan, stats["suggested_mode"])
    write_json(job_dir / "stats.json", stats)
    update_status(job_dir, stage="prepared", code="OK", message=f"已截取 {args.frm}–{args.to}", chars=stats["chars"], chunks=len(plan))
    print(json.dumps({"job_dir": str(job_dir), "stage": "prepared", "chars": stats["chars"], "chunks": len(plan)}, ensure_ascii=False))
    return 0


def next_step(job_dir: Path, st: dict) -> str:
    stage = st.get("stage")
    if stage in {"failed", "needs_vision", "needs_transcribe"}:
        return {"failed": f"处理失败（{st.get('code')}）：{st.get('message')}",
                "needs_vision": "需要看图：交给看图流程", "needs_transcribe": "等待转写"}[stage]
    if stage in {"acquiring", "transcribing"}:
        return "准备中"
    if stage == "prepared":
        return "有重复成品，先问用户" if st.get("code") == "DUPLICATE" else "查询（写 sheet.json）"
    if stage == "surveyed":
        tasks = read_json(job_dir / "tasks.json", [])
        pending = [t["chunk"] for t in tasks if not (job_dir / t["output"]).exists()]  # 一个代理做完时不写笔记
        return f"写作：{', '.join(pending)}" if pending else "收尾"
    if stage == "assembled":
        return "检查与发布"
    if stage == "published":
        return f"完成：{st.get('output')}"
    return stage or "未知"


def cmd_status(args) -> int:
    dirs = []
    for p in args.paths:
        p = Path(p).expanduser()
        if (p / "status.json").exists():
            dirs.append(p)
        else:
            dirs.extend(sorted(x.parent for x in p.glob("*/status.json")))
    for d in dirs:
        st = read_json(d / "status.json", {})
        meta = read_json(d / "meta.json", {}) or {}
        title = (meta.get("title") or st.get("input") or d.name)[:28]
        size = f"{st.get('chars', 0):,} 字" if st.get("chars") else ""
        print(f"{d.name} | {title} | {st.get('stage')} | {st.get('mode') or st.get('mode_suggested') or ''} | "
              f"{size} | {st.get('chunks') or ''} 块 | 下一步：{next_step(d, st)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--input", required=True)
    p.add_argument("--job-dir", required=True)
    p.add_argument("--output-dir")
    p.add_argument("--language", default="auto")
    p.add_argument("--chunk-chars", type=int)
    p.add_argument("--no-transcribe", action="store_true")
    p.add_argument("--whisper-model")
    p.add_argument("--cookies-profile")
    p.add_argument("--cookies-file")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("apply-sheet")
    p.add_argument("--job-dir", required=True)
    p.set_defaults(func=cmd_apply_sheet)
    p = sub.add_parser("select")
    p.add_argument("--job-dir", required=True)
    p.add_argument("--from", dest="frm", required=True)
    p.add_argument("--to", required=True)
    p.set_defaults(func=cmd_select)
    p = sub.add_parser("status")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_status)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
