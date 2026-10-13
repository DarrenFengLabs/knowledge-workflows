#!/usr/bin/env python3
"""全文剪藏 · 拼装与发布（不花模型额度）。

用法（在 skill 根目录运行）:
  python3 scripts/assemble.py digest --job-dir <任务目录>     # 收尾开工时一次看完要用的材料
  python3 scripts/assemble.py build --job-dir <任务目录>
  python3 scripts/assemble.py publish --job-dir <任务目录> --output-dir <成品目录> [--overwrite | --rename] [--force]

build 读 meta.json、sheet.json、header.md（大纲、术语）、out/*（正文）、tail.md（补充细节），
生成 final.md：背景信息表由脚本按元信息生成，规模里的字数按成品正文实数计算；
翻译档默认只出中文；用户要双语时，英文干净的块按段落编号把英文原文（小字）与中文译文配对。
小标题行末尾的段号（〔s12〕）换成原片时间点，YouTube、B站做成可点击的跳转链接。
digest 把收尾要看的东西压成一屏：工作单要点、互动数、各块小标题和笔记、全部〔?〕、断在句中的接缝、读者画像。
publish 按“发布日期_中文标题.md”命名，同名不覆盖（除非 --overwrite 或 --rename），写入后回读核对。
检查脚本还有违规时拒绝发布，除非加 --force（会记入状态）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cliplib.common import (  # noqa: E402
    BEIJING, count_cjk, minutes_text, parse_hms, parse_marker, read_json, read_segments, sanitize_filename,
    strip_markup, update_status,
)

SCRIPT_DIR = Path(__file__).resolve().parent
HEADING_MARK = re.compile(r"^(#{2,4} .*?)[ \t]*〔([sp]\d+)〕[ \t]*$", re.M)


def cell(v) -> str:
    return str(v or "").replace("|", "／").replace("\n", " ").strip()


def size_text(body: str, bilingual: bool) -> str:
    def zh(n: int) -> str:
        return f"约 {n / 10000:.1f} 万字" if n >= 10000 else f"约 {round(n, -2) or n:,} 字"
    if bilingual:
        en = " ".join(re.findall(r"<small>(.*?)</small>", body, re.S))
        words = len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", strip_markup(en)))
        zh_chars = count_cjk(strip_markup(re.sub(r"<small>.*?</small>", "", body, flags=re.S)))
        return f"英文约 {round(words, -2) or words:,} 词 · 中文{zh(zh_chars)}"
    plain = re.sub(r"\s", "", strip_markup(body))
    return f"正文{zh(len(plain))}"


def background(meta: dict, sheet: dict, body: str, bilingual: bool) -> str:
    title = sheet.get("title") or meta.get("title") or "未注明"
    url = sheet.get("url") if "url" in sheet else meta.get("url")
    title_cell = f"[{cell(title)}]({url})" if url else cell(title)
    published = sheet.get("published") or meta.get("published_date") or "未注明"
    duration = minutes_text(meta.get("duration_s"))
    scale = " · ".join(x for x in [duration, size_text(body, bilingual)] if x)
    rows = [("原标题", title_cell), ("来源", cell(sheet.get("source_line") or meta.get("source") or "未注明")),
            ("发布时间", cell(published)), ("规模", scale)]
    if sheet.get("speakers_line"):
        rows.append(("说话人", cell(sheet["speakers_line"])))
    lines = ["## 背景信息", "", "| 项目 | 内容 |", "| --- | --- |"] + [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(lines)


def merge_translation(job: Path, chunk: str, zh_file: Path, bilingual: bool) -> tuple[str, list[str]]:
    """英文干净时：写作代理只写带段号的中文；要双语时这里按段号配上英文原文，否则去掉段号只留中文。"""
    en = {s["id"]: s for s in read_segments(job / "chunks" / f"{chunk}.md")}
    out, seen = [], set()
    for block in re.split(r"\n\s*\n", zh_file.read_text(encoding="utf-8").strip()):
        parsed = parse_marker(block.strip())
        if not parsed:
            out.append(block.strip())
            continue
        seg_id, _, _, zh = parsed
        seen.add(seg_id)
        src = en.get(seg_id)
        if bilingual and src is not None:
            out.append(f"<small>{src['text']}</small>")
        out.append(zh)
    missing = [k for k in en if k not in seen]
    return "\n\n".join(out), missing


def time_label(meta: dict, ts: str) -> str:
    """00:12:34 → 12:34；YouTube、B站给出跳到该时间点的链接，其他平台只写时间。"""
    sec = int(parse_hms(ts))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    label = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    url = meta.get("url") or ""
    if meta.get("platform") == "youtube" and url:
        return f"[{label}]({url}{'&' if '?' in url else '?'}t={sec}s)"
    if meta.get("platform") == "bilibili" and url:
        return f"[{label}]({url.split('?')[0]}?t={sec})"
    return label


def anchor_headings(text: str, ts_map: dict, meta: dict) -> str:
    """小标题行末尾的段号换成原片时间点；没有时间的（文章段号）直接去掉。"""
    def repl(m):
        ts = ts_map.get(m.group(2))
        return f"{m.group(1)} · {time_label(meta, ts)}" if ts else m.group(1)
    return HEADING_MARK.sub(repl, text)


def body_text(job: Path, sheet: dict, meta: dict) -> tuple[str, list[str]]:
    tasks = read_json(job / "tasks.json", []) or []
    missing_all: list[str] = []
    if not tasks:
        f = job / "out" / "001.md"
        return (f.read_text(encoding="utf-8").strip() if f.exists() else ""), []
    ts_map = {s["id"]: s["ts"] for s in read_segments(job / "source.md") if s.get("ts")} \
        if (job / "source.md").exists() else {}
    parts = []
    for t in tasks:
        out = job / t["output"]
        if not out.exists():
            missing_all.append(f"{t['chunk']}（没有产出）")
            continue
        if t["kind"] == "翻译-整理":
            text, missing = merge_translation(job, t["chunk"], out, t.get("bilingual", True))
            missing_all += [f"{t['chunk']}:{m}" for m in missing]
        else:
            text = out.read_text(encoding="utf-8").strip()
        parts.append(anchor_headings(text, ts_map, meta))
    return "\n\n".join(p for p in parts if p), missing_all


def find_local(name: str) -> Path | None:
    for base in [Path.cwd(), *Path.cwd().parents, SCRIPT_DIR, *SCRIPT_DIR.parents]:
        f = base / ".local" / name
        if f.exists():
            return f
    return None


def cmd_digest(args) -> int:
    """收尾开工时运行一次：把要用的材料压成一屏，代替逐个打开工作单、笔记和各块产出。"""
    job = Path(args.job_dir).expanduser()
    sheet = read_json(job / "sheet.json", {}) or {}
    meta = read_json(job / "meta.json", {}) or {}
    tasks = read_json(job / "tasks.json", []) or []
    lines = ["== 工作单",
             f"类型 {sheet.get('content_type')} · 档位 {sheet.get('mode')} · 说话人 {sheet.get('speaker_mode')} · "
             f"双语 {'是' if sheet.get('bilingual') else '否'} · 共 {len(tasks) or 1} 块"]
    if sheet.get("speakers_line"):
        lines.append(f"说话人：{sheet['speakers_line']}")
    for r in sheet.get("related") or []:
        lines.append(f"相关：{r.get('title')}（{r.get('why')}）{r.get('url_or_file') or ''}")
    if sheet.get("notes"):
        lines.append(f"给写作的话：{sheet['notes']}")
    extra = []
    if meta.get("interaction"):
        extra.append("互动 " + json.dumps(meta["interaction"], ensure_ascii=False))
    if meta.get("images_skipped"):
        extra.append(f"跳过无说明图片 {meta['images_skipped']} 张")
    if extra:
        lines += ["== 元信息"] + extra
    if not tasks:
        lines.append("整理档：正文由脚本原样搬入 out/001.md，读它画下划线、删引导语、改个别错字。")
    outs = [(t["chunk"], job / t["output"]) for t in tasks] or [("001", job / "out" / "001.md")]
    lines.append("== 各块小标题")
    for k, f in outs:
        heads = re.findall(r"^#{3} (.+)$", f.read_text(encoding="utf-8"), re.M) if f.exists() else ["（没有产出）"]
        lines.append(f"{k}：" + (" / ".join(HEADING_MARK.sub(r"\1", "### " + h)[4:] for h in heads) or "（无，延续上一块）"))
    notes = [(f.stem, read_json(f, {}) or {}) for f in sorted((job / "notes").glob("*.json"))]
    if notes:
        lines.append("== 笔记")
        for k, n in notes:
            for key, fmt in [("sections", lambda x: f"{x.get('heading')}：{x.get('gist')}"),
                             ("terms", lambda x: x.get("term", "") + ("（需解释）" if x.get("explain") else "")),
                             ("doubts", lambda x: f"{x.get('text')}——{x.get('why')}"),
                             ("fixes", lambda x: f"{x.get('from')}→{x.get('to')}"),
                             ("omitted", lambda x: f"{x.get('what')}（{x.get('where')}）"),
                             ("visuals", lambda x: x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)),
                             ("model_mentions", lambda x: f"{x.get('name')}：{x.get('anchor')}")]:
                vals = n.get(key) or []
                if vals:
                    shown = [fmt(v) for v in vals[:12]] + ([f"等 {len(vals)} 条"] if len(vals) > 12 else [])
                    lines.append(f"{k} {key}：" + "；".join(shown))
    doubts = []
    for k, f in outs:
        if f.exists():
            text = f.read_text(encoding="utf-8")
            doubts += [f"{k}：…{text[max(0, m.start() - 24):m.start()]}〔?〕{text[m.end():m.end() + 10]}…".replace("\n", " ")
                       for m in re.finditer(r"〔\?〕", text)]
    lines.append(f"== 〔?〕共 {len(doubts)} 处")
    lines += doubts[:40] + ([f"……其余 {len(doubts) - 40} 处略"] if len(doubts) > 40 else [])
    seams = []
    for (k, f), (k2, f2) in zip(outs, outs[1:]):
        if f.exists() and f2.exists():
            tail = f.read_text(encoding="utf-8").strip()
            bare = re.sub(r"</?u>|</?small>", "", tail)
            if bare and not re.search(r"[。！？!?.…”」）)\]]$", bare):
                head = f2.read_text(encoding="utf-8").strip()
                seams.append(f"{k} 结尾：…{tail[-60:]}\n{k2} 开头：{head[:60]}…".replace("\n\n", " "))
    if seams:
        lines += ["== 断在句中的接缝"] + seams
    reader = find_local("reader.md")
    if reader:
        lines += ["== 读者画像", reader.read_text(encoding="utf-8").strip()]
    print("\n".join(lines))
    return 0


def cmd_build(args) -> int:
    job = Path(args.job_dir).expanduser()
    meta = read_json(job / "meta.json", {}) or {}
    sheet = read_json(job / "sheet.json", {}) or {}
    body, missing = body_text(job, sheet, meta)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    bilingual = "<small>" in body
    parts = [background(meta, sheet, body, bilingual)]
    header = job / "header.md"
    if header.exists() and header.read_text(encoding="utf-8").strip():
        parts.append(header.read_text(encoding="utf-8").strip())
    parts.append("## 正文\n\n" + body)
    tail = job / "tail.md"
    if tail.exists() and tail.read_text(encoding="utf-8").strip():
        parts.append(tail.read_text(encoding="utf-8").strip())
    final = "\n\n".join(parts).rstrip() + "\n"
    (job / "final.md").write_text(final, encoding="utf-8")
    update_status(job, stage="assembled", final=str(job / "final.md"), assemble_missing=missing)
    print(json.dumps({"final": str(job / "final.md"), "missing": missing, "bilingual": bilingual,
                      "chars": len(re.sub(r"\s", "", strip_markup(body)))}, ensure_ascii=False))
    if missing:
        print("缺段或缺块：" + "、".join(missing[:20]), file=sys.stderr)
        return 1
    return 0


def cmd_publish(args) -> int:
    job = Path(args.job_dir).expanduser()
    final = job / "final.md"
    if not final.exists():
        print("先运行 build", file=sys.stderr)
        return 2
    check = read_json(job / "check.json")
    if not check:
        print("先运行 check_clip.py --job-dir", file=sys.stderr)
        return 2
    if check.get("violations") and not args.force:
        print(f"检查还有 {check['violations']} 项违规，未发布（确需发布加 --force，并在报告里说明）", file=sys.stderr)
        return 1
    meta = read_json(job / "meta.json", {}) or {}
    sheet = read_json(job / "sheet.json", {}) or {}
    date = sheet.get("published") or meta.get("published_date") or ""
    m = re.match(r"\d{4}-\d{2}-\d{2}", date)
    if m:
        date = m.group(0)
    else:
        date = dt.datetime.now(BEIJING).date().isoformat()
        print(f"没有可靠的发布日期，文件名用整理日 {date}", file=sys.stderr)
    title = sanitize_filename(sheet.get("title_zh") or meta.get("title") or "未命名")
    out_dir = Path(args.output_dir).expanduser()
    target = out_dir / f"{date}_{title}.md"
    if target.exists():
        if args.overwrite:
            shutil.copy2(target, job / "raw" / f"overwritten_{target.name}")
        elif args.rename:
            k = 2
            while (out_dir / f"{date}_{title} ({k}).md").exists():
                k += 1
            target = out_dir / f"{date}_{title} ({k}).md"
        else:
            update_status(job, stage="assembled", code="CONFLICT", message=f"已有同名文件：{target.name}")
            print(json.dumps({"code": "CONFLICT", "target": str(target)}, ensure_ascii=False))
            return 3
    data = final.read_bytes()
    target.write_bytes(data)
    ok = hashlib.sha256(target.read_bytes()).hexdigest() == hashlib.sha256(data).hexdigest()
    if not ok:
        print("写入后回读不一致", file=sys.stderr)
        return 2
    update_status(job, stage="published", code="OK", output=str(target), forced=bool(args.force and check.get("violations")))
    print(json.dumps({"published": str(target), "bytes": len(data)}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("digest")
    p.add_argument("--job-dir", required=True)
    p.set_defaults(func=cmd_digest)
    p = sub.add_parser("build")
    p.add_argument("--job-dir", required=True)
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("publish")
    p.add_argument("--job-dir", required=True)
    p.add_argument("--output-dir", required=True)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--overwrite", action="store_true")
    g.add_argument("--rename", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_publish)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
