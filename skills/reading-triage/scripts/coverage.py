#!/usr/bin/env python3
"""按字数报覆盖率 —— 铁律 §1.1。

用法:
  python3 coverage.py txt/ --read 全文A 全文B --partial 大文C:0.3
说明:
  --read     完整读过的文件名关键词
  --partial  只读了一部分的,写 关键词:比例
输出既给篇数覆盖率也给字数覆盖率,后者才是要报给用户的那个。
"""
import sys, glob, os, argparse

ap = argparse.ArgumentParser()
ap.add_argument("dir"); ap.add_argument("--read", nargs="*", default=[])
ap.add_argument("--partial", nargs="*", default=[])
a = ap.parse_args()

files = {os.path.basename(f): len(open(f, encoding="utf-8", errors="ignore").read())
         for f in glob.glob(os.path.join(a.dir, "*.txt"))}
total = sum(files.values())
if not files:
    sys.exit(f"目录里没有 .txt 文件,无法算覆盖率: {a.dir}\n"
             f"(抓取脚本默认就写 .txt;若正文是别的扩展名,先转成 .txt 再跑)")
if total == 0:
    sys.exit(f"{len(files)} 个 .txt 全是空文件,抓取多半失败了,先查抓取: {a.dir}")
part = {}
for p in a.partial:
    k, _, r = p.rpartition(":"); part[k] = float(r)

read_chars = 0; n_full = 0
for name, n in files.items():
    if any(k in name for k in a.read): read_chars += n; n_full += 1
    else:
        for k, r in part.items():
            if k in name: read_chars += int(n * r); break

print(f"文件数        {len(files)}")
print(f"总字数        {total:,}")
print(f"篇数覆盖率     {n_full}/{len(files)} = {n_full/len(files):.0%}   ← 会骗人,别只报这个")
print(f"字数覆盖率     {read_chars:,}/{total:,} = {read_chars/total:.0%}   ← 报这个")
big = sorted(((n, k) for k, n in files.items()), reverse=True)[:8]
print("\n最长的 8 篇(优先确认是否真读完):")
for n, k in big: print(f"  {n:7,} 字  {k}")
