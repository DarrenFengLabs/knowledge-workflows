#!/usr/bin/env python3
"""写完一篇笔记后的"量化自检 + 飞书强调标记体检"脚本。

用法:
    python3 check_format.py 笔记.md
    python3 check_format.py 笔记目录/*.md      # 批量

它做两件事：
1) 体检飞书 `*斜体*` / `**加粗**` 是否会失效——CommonMark/飞书规则下，强调标记的
   **紧贴侧字符**若是空格、换行、任何全角标点、或引号，强调就不渲染、原样显示星号。
   口诀：开标记看右侧、闭标记看左侧，紧贴侧必须是"实义文字"。命中即打印、计入"违规"。
   （正常应为 0：落笔时就把标点/引号甩到标记外侧，脚本只是最后一道保险。）
2) 报出**斜体数 / 金句引用块数 / 表格数 / 字数**——这些是"强调密度"的体检指标。
   用相对判据看：和当天内容量是否匹配；明显偏少或整篇滑成"列点平铺"，就回去补
   斜体/金句/表格。数字是参考信号、不是必须达到的硬门槛。

退出码：有任何强调标记违规则返回 1，便于在脚本里串联。
"""
import re
import sys
import glob

# 紧贴侧若是这些字符，飞书强调失效
BAD = set(' \t\n　。，、；：？！…—·（）【】「」『』《》〈〉') | set(
    ['"', "'", '“', '”', '‘', '’']
)


def check_one(path):
    s = open(path, encoding='utf-8').read()
    bad = 0

    # 加粗 **...**
    for m in re.finditer(r'\*\*([^*\n]+?)\*\*', s):
        inner = m.group(1)
        if inner and (inner[0] in BAD or inner[-1] in BAD):
            print(f'  加粗X: {inner[:32]}')
            bad += 1

    # 斜体 *...*（排除 ** 的单星）
    pos = [
        i for i in range(len(s))
        if s[i] == '*'
        and (i == 0 or s[i - 1] != '*')
        and (i + 1 >= len(s) or s[i + 1] != '*')
    ]
    for k in range(0, len(pos) - 1, 2):
        a, b = pos[k], pos[k + 1]
        if s[a + 1] in BAD or s[b - 1] in BAD:
            print(f'  斜体X: {s[a + 1:b][:32]}')
            bad += 1

    italics = len(pos) // 2
    quotes = len(re.findall(r'(?m)^>', s))
    tables = s.count('|---')
    chars = len(s)
    bold_paired = s.count('**') % 2 == 0
    star_paired = len(pos) % 2 == 0

    print(f'[{path}]')
    print(
        f'  违规: {bad} | **成对: {bold_paired} | *单星成对: {star_paired}'
    )
    print(
        f'  斜体: {italics} | 金句>块: {quotes} | 表格行: {tables} | 字数: {chars}'
    )
    return bad


def main(argv):
    args = argv[1:]
    if not args:
        sys.exit(__doc__)
    files = []
    for a in args:
        files.extend(glob.glob(a) or [a])
    total_bad = 0
    for f in files:
        total_bad += check_one(f)
    sys.exit(1 if total_bad else 0)


if __name__ == '__main__':
    main(sys.argv)
