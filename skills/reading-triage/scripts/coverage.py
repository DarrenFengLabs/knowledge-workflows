#!/usr/bin/env python3
"""按 ID 合并候选账本和各批结果，核对每条的决定，按字数算覆盖率。

用法:
  python3 coverage.py <任务目录>/ledger.jsonl <任务目录>/screen/*.jsonl <任务目录>/read/*.jsonl [--final]

第一个文件是候选账本，后面是各批结果（抽检改判放在最后）。每个文件每行一个 JSON，
按 id 合并，后面的文件覆盖前面的同名字段。用到的字段:
  decision     预筛决定：排除代码 H R P V C D，或读法 摘要 段落 全文
  ref          判 R 时的代表条目 ID
  grade        精读后的等级：必读 值得知道 收录 未评估，或排除代码
  chars_total  原文字数，不知道写 null
  chars_read   实际读到的字数
--final：交付前用，还没预筛、还没定级的也算不合规。有不合规时退出码为 1。
篇数覆盖率会骗人，报给用户的是字数覆盖率。
"""
import argparse
import json
import sys

EXCLUDE = set("HRPVCD")
DEPTH = {"摘要", "段落", "全文"}
GRADES = {"必读", "值得知道", "收录", "未评估"}
READ_GRADES = {"必读", "值得知道", "收录"}
ORDER = ["必读", "值得知道", "收录", "只读了摘要", "未评估", "读后排除", "排除", "待读", "待筛"]


def num(v):
    """字数字段：None 或非负整数；其他值返回 False 表示不合规。"""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        return False
    return int(v)


def load(paths):
    items, order, problems = {}, [], []
    for i, path in enumerate(paths):
        try:
            f = open(path, encoding="utf-8")
        except OSError as e:
            sys.exit(f"读不了 {path}: {e}")
        seen = set()
        with f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    problems.append(f"{path}:{n} 不是合法的 JSON")
                    continue
                rid = row.get("id") if isinstance(row, dict) else None
                if not rid:
                    problems.append(f"{path}:{n} 没有 id")
                    continue
                if rid in seen:
                    problems.append(f"{path}:{n} {rid} 在同一个文件里出现两次")
                seen.add(rid)
                if rid not in items:
                    if i > 0:
                        problems.append(f"{path}:{n} {rid} 不在账本里")
                    items[rid] = {}
                    order.append(rid)
                items[rid].update(row)
    return items, order, problems


def where(it):
    d, g = it.get("decision"), it.get("grade")
    if g in GRADES:
        return g
    if g in EXCLUDE:
        return "读后排除"
    if d in EXCLUDE:
        return "排除"
    if d == "摘要":
        return "只读了摘要"
    if d in DEPTH:
        return "待读"
    return "待筛"


def main():
    ap = argparse.ArgumentParser(description="合并账本、核对决定、按字数算覆盖率")
    ap.add_argument("files", nargs="+", help="账本在前，各批结果在后")
    ap.add_argument("--final", action="store_true", help="交付前用：没预筛、没定级的也算不合规")
    ap.add_argument("--show", type=int, default=8, help="每类清单列几条（默认 8）")
    a = ap.parse_args()

    items, order, problems = load(a.files)
    if not items:
        sys.exit("账本里没有条目，先检查文件路径")

    counts = {k: [] for k in ORDER}
    total_known = kept_known = read = 0
    unknown, partial, full_n = [], [], 0
    for rid in order:
        it = items[rid]
        d, g = it.get("decision"), it.get("grade")
        if d is not None and d not in EXCLUDE | DEPTH:
            problems.append(f"{rid} 的 decision 不在允许的取值里：{d}")
        if g is not None and g not in GRADES | EXCLUDE:
            problems.append(f"{rid} 的 grade 不在允许的取值里：{g}")
        if "R" in (d, g) and not it.get("ref"):
            problems.append(f"{rid} 判了重复，但没写代表条目（ref）")
        ct, cr = num(it.get("chars_total")), num(it.get("chars_read"))
        if ct is False or cr is False:
            problems.append(f"{rid} 的字数不是非负整数")
            ct = None if ct is False else ct
            cr = None if cr is False else cr
        if g in READ_GRADES and not cr:
            problems.append(f"{rid} 定为「{g}」，却没有读原文的记录（chars_read 为空或 0）")
        if cr and ct is None:
            problems.append(f"{rid} 记了已读字数，却没记原文字数")
        if cr and ct is not None and cr > ct:
            problems.append(f"{rid} 已读字数 {cr} 超过原文字数 {ct}")

        w = where(it)
        counts[w].append(rid)
        if a.final and (w == "待读" or (w == "待筛" and d is None)):
            problems.append(f"{rid} 还{'没预筛' if w == '待筛' else '没定级'}")
        if ct is None:
            unknown.append(rid)
            continue
        total_known += ct
        if d in DEPTH:
            kept_known += ct
        r = min(cr or 0, ct)
        read += r
        if ct and r >= ct:
            full_n += 1
        elif d in DEPTH and w != "只读了摘要":
            partial.append((ct, r, rid))

    def pct(x, y):
        return f"{x:,}/{y:,} = {x / y:.0%}" if y else "分母为 0，算不了"

    print(f"候选总数      {len(order)}")
    for k in ORDER:
        if counts[k]:
            print(f"  {k:<8}{len(counts[k])}")
    print(f"字数已知      {len(order) - len(unknown)} 条，共 {total_known:,} 字；字数未知 {len(unknown)} 条（不进分母）")
    print(f"候选集字数覆盖率  {pct(read, total_known)}   ← 报这个")
    print(f"决策集字数覆盖率  {pct(read, kept_known)}   ← 和这个")
    print(f"读完的篇数      {full_n}/{len(order)}   ← 会骗人，别只报这个")
    if unknown:
        more = f" 等 {len(unknown)} 条" if len(unknown) > a.show else ""
        print(f"\n字数未知：{'、'.join(unknown[:a.show])}{more}")
    if partial:
        print(f"\n保留项里最长、还没读完的 {min(a.show, len(partial))} 篇（确认是不是真的只需要读这些）：")
        for ct, r, rid in sorted(partial, reverse=True)[:a.show]:
            print(f"  {rid}  原文 {ct:,} 字，读了 {r:,} 字（{r / ct:.0%}）")
    if problems:
        print(f"\n不合规 {len(problems)} 处：")
        for p in problems[: max(a.show, 30)]:
            print(f"  {p}")
        if len(problems) > max(a.show, 30):
            print(f"  ……还有 {len(problems) - max(a.show, 30)} 处")
        sys.exit(1)
    print("\n没有不合规的条目。")


if __name__ == "__main__":
    main()
