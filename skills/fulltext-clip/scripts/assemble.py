#!/usr/bin/env python3
"""全文剪藏 · 拼装与发布（不花模型额度）。

用法（在 skill 根目录运行）:
  python3 scripts/assemble.py build --job-dir <任务目录>
  python3 scripts/assemble.py publish --job-dir <任务目录> --output-dir <成品目录> [--overwrite | --rename] [--force]

build 读 meta.json、sheet.json、header.md（大纲、术语）、out/*（正文）、tail.md（补充细节），
生成 final.md：背景信息表由脚本按元信息生成，规模里的字数按成品正文实数计算；
翻译档里英文干净的块，按段落编号把英文原文（小字）与中文译文配对。
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
    BEIJING, count_cjk, minutes_text, parse_marker, read_json, read_segments, sanitize_filename, strip_markup,
    update_status,
)


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


def merge_translation(job: Path, chunk: str, zh_file: Path) -> tuple[str, list[str]]:
    """英文干净时：写作代理只写带段号的中文，这里把英文原文按段号配上。"""
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
        if src is None:
            out.append(zh)
            continue
        out.append(f"<small>{src['text']}</small>")
        out.append(zh)
    missing = [k for k in en if k not in seen]
    return "\n\n".join(out), missing


def body_text(job: Path, sheet: dict) -> tuple[str, list[str]]:
    tasks = read_json(job / "tasks.json", []) or []
    missing_all: list[str] = []
    if not tasks:
        f = job / "out" / "001.md"
        return (f.read_text(encoding="utf-8").strip() if f.exists() else ""), []
    parts = []
    for t in tasks:
        out = job / t["output"]
        if not out.exists():
            missing_all.append(f"{t['chunk']}（没有产出）")
            continue
        if t["kind"] == "翻译-整理":
            text, missing = merge_translation(job, t["chunk"], out)
            missing_all += [f"{t['chunk']}:{m}" for m in missing]
            parts.append(text)
        else:
            parts.append(out.read_text(encoding="utf-8").strip())
    return "\n\n".join(p for p in parts if p), missing_all


def cmd_build(args) -> int:
    job = Path(args.job_dir).expanduser()
    meta = read_json(job / "meta.json", {}) or {}
    sheet = read_json(job / "sheet.json", {}) or {}
    body, missing = body_text(job, sheet)
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
