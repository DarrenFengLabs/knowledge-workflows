#!/usr/bin/env python3
"""按页从 .pptx 抽取纯文本（课件/PPT），仅用 Python 标准库（zipfile + xml），不依赖 python-pptx。

用法:
    python3 extract_pptx.py "课件/某.pptx"                 # 打印到 stdout，按页分隔
    python3 extract_pptx.py "课件/*.pptx" -o out.txt        # glob + 合并写入
    python3 extract_pptx.py "某.pptx" --notes               # 连演讲者备注一起抽（按 rels 精确对应该页）

说明:
- .pptx 内部是 zip，幻灯片正文在 ppt/slides/slideN.xml，文本在 DrawingML 的 <a:t> 里。
- 按文件名里的页号数字排序输出（slide1, slide2, ...），每页一个 "--- 第N页 ---" 分隔。
  极少数 PPT 的"文件页号"与"放映顺序"不一致（放映顺序定义在 presentation.xml）；做笔记够用，
  发现错位以页内容为准。
- 只抽文本，不抽图片/图表/示意图——这些请配合截图用视觉读（见 SKILL §3 媒体处理）。
"""
import sys
import re
import glob
import zipfile
from xml.etree import ElementTree as ET

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def _slide_no(name):
    m = re.search(r'(\d+)\.xml$', name)
    return int(m.group(1)) if m else 0


def _text_from_xml(data):
    root = ET.fromstring(data)
    lines = []
    for p in root.iter(f'{A}p'):           # 每个段落一行，保留要点分行
        runs = [t.text or '' for t in p.iter(f'{A}t')]
        line = ''.join(runs).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines)


def _notes_target(z, slide_name):
    """返回该页对应的 notesSlide 路径（按 rels 精确对应），没有则 None。"""
    rels = 'ppt/slides/_rels/' + slide_name.split('/')[-1] + '.rels'
    if rels not in z.namelist():
        return None
    root = ET.fromstring(z.read(rels))
    for r in root:
        tgt = r.get('Target', '')
        if 'notesSlide' in tgt:
            return 'ppt/' + tgt.replace('../', '')   # ../notesSlides/x.xml -> ppt/notesSlides/x.xml
    return None


def extract(path, notes=False):
    out = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        slides = sorted(
            [n for n in names if re.match(r'ppt/slides/slide\d+\.xml$', n)],
            key=_slide_no,
        )
        for i, s in enumerate(slides, 1):
            block = [f'--- 第{i}页 ---', _text_from_xml(z.read(s))]
            if notes:
                nt_path = _notes_target(z, s)
                if nt_path and nt_path in names:
                    nt = _text_from_xml(z.read(nt_path))
                    if nt:
                        block.append(f'〔备注〕{nt}')
            out.append('\n'.join(b for b in block if b))
    return '\n\n'.join(out)


def main(argv):
    args = argv[1:]
    if not args:
        sys.exit(__doc__)

    out_path = None
    notes = False
    sep = False
    files = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ('-o', '--out'):
            out_path = args[i + 1]
            i += 2
            continue
        if a == '--notes':
            notes = True
            i += 1
            continue
        if a == '--sep':
            sep = True
            i += 1
            continue
        files.extend(glob.glob(a) or [a])
        i += 1

    chunks = []
    for f in files:
        try:
            text = extract(f, notes=notes)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f'[warn] 跳过 {f}: {e}\n')
            continue
        if sep or len(files) > 1:
            chunks.append(f'===== {f} =====\n{text}')
        else:
            chunks.append(text)

    result = '\n\n'.join(chunks)
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as fh:
            fh.write(result)
        sys.stderr.write(f'[ok] wrote {len(files)} file(s) -> {out_path}\n')
    else:
        print(result)


if __name__ == '__main__':
    main(sys.argv)
