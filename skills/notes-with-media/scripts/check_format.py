#!/usr/bin/env python3
"""写完一篇笔记后的"量化自检 + 飞书强调标记体检"脚本。

用法:
    python3 check_format.py 笔记.md
    python3 check_format.py 笔记目录/*.md      # 批量

它做两件事：
1) 体检飞书 `*斜体*` / `**加粗**` 是否会失效——CommonMark/飞书规则下，强调标记的
   紧贴侧字符若是空格、换行、任何全角标点、或引号，强调就不渲染、原样显示星号。
   口诀：开标记看右侧、闭标记看左侧，紧贴侧必须是"实义文字"。
   失效、未闭合的强调，以及 `#` 一级标题（飞书层级会乱），都计入"违规"。（正常应为 0：落笔时就把标点/引号甩到标记外侧，
   脚本只是最后一道保险。）
2) 报出斜体数 / 金句块数 / 表格数 / 字数——这些是"强调密度"的体检指标。
   用相对判据看：和当天内容量是否匹配；明显偏少或整篇滑成"列点平铺"，就回去补
   斜体/金句/表格。数字是参考信号、不是必须达到的硬门槛。

按行解析，不把这些星号当强调：代码块和行内代码里的星号、行首的列表星号、
转义的 \\*、分隔线 ***。金句块按连续的 `>` 行计一块；表格按分隔行计一张
（`|---|`、`| --- |`、`|:---:|` 等写法都认）。

退出码：有任何违规则返回 1，便于在脚本里串联。
"""
import re
import sys
import glob

# 紧贴侧若是这些字符，飞书强调失效
BAD = set(' \t\n　。，、；：？！…—·（）【】「」『』《》〈〉') | set(
    ['"', "'", '“', '”', '‘', '’']
)

FENCE = re.compile(r'^\s*(```|~~~)')
INLINE_CODE = re.compile(r'`[^`\n]*`')
LIST_STAR = re.compile(r'^(\s*)\*(?=\s)')
RULE = re.compile(r'^\s*(\*\s*){3,}$')
BOLD = re.compile(r'\*\*([^*\n]+?)\*\*')
H1 = re.compile(r'^#\s')
TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$')


def _bad_edge(inner):
    return not inner or inner[0] in BAD or inner[-1] in BAD


def analyze(text):
    """返回 {'problems': [(行号, 类型, 摘录)], 'italics', 'quotes', 'tables', 'chars'}。"""
    problems = []
    italics = quotes = tables = 0
    in_code = False
    prev_quote = False

    for no, raw in enumerate(text.splitlines(), 1):
        if FENCE.match(raw):
            in_code = not in_code
            prev_quote = False
            continue
        if in_code:
            continue

        if H1.match(raw):
            problems.append((no, '一级标题', raw.strip()[:32]))

        is_quote = raw.lstrip().startswith('>')
        if is_quote and not prev_quote:
            quotes += 1
        prev_quote = is_quote

        if '|' in raw and TABLE_SEP.match(raw):
            tables += 1
            continue
        if RULE.match(raw):
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
            italics += 1
            if _bad_edge(line[a + 1:b]):
                problems.append((no, '斜体失效', line[a + 1:b][:32]))
        if len(stars) % 2:
            problems.append((no, '斜体未闭合', raw.strip()[:32]))

    return {
        'problems': problems,
        'italics': italics,
        'quotes': quotes,
        'tables': tables,
        'chars': len(text),
    }


def check_one(path):
    r = analyze(open(path, encoding='utf-8').read())
    print(f'[{path}]')
    for no, kind, excerpt in r['problems']:
        print(f'  第 {no} 行 {kind}: {excerpt}')
    print(f"  违规: {len(r['problems'])}")
    print(
        f"  斜体: {r['italics']} | 金句块: {r['quotes']} | "
        f"表格: {r['tables']} | 字数: {r['chars']}"
    )
    return len(r['problems'])


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
