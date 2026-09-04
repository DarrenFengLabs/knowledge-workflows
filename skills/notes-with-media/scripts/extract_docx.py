#!/usr/bin/env python3
"""按段落从 .docx 抽取纯文本，仅用 Python 标准库（zipfile + xml），不依赖 pandoc/python-docx，不丢行。

用法:
    python3 extract_docx.py "path/to/file.docx"               # 打印到 stdout
    python3 extract_docx.py "a.docx" "b.docx" -o out.txt       # 多个文件合并写入
    python3 extract_docx.py "录音/*.docx" --sep                # glob + 文件分隔标记

说明:
- ASR/会议转写的 .docx 常见两种排版：①"说话人N + 时间戳 + 文本" ②无标注的连续长段。
  本脚本只负责把段落文本原样抽出来，**不做清洗**——说话人与时间戳是否可信由你判断
  （转写整理时通常二者都不可信，按内容区分即可）。
- 输出每个 docx 段落一行，便于后续用 `sed -n '起,止p'` 分批切片读取。
"""
import sys
import glob
import zipfile
from xml.etree import ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def extract(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read('word/document.xml')
    root = ET.fromstring(xml)
    body = root.find(f'{W}body')
    lines = []
    for p in body.iter(f'{W}p'):
        texts = [t.text or '' for t in p.iter(f'{W}t')]
        lines.append(''.join(texts))
    return '\n'.join(lines)


def main(argv):
    args = argv[1:]
    if not args:
        sys.exit(__doc__)

    out_path = None
    sep = False
    files = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ('-o', '--out'):
            out_path = args[i + 1]
            i += 2
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
            text = extract(f)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f'[warn] 跳过 {f}: {e}\n')
            continue
        if sep:
            chunks.append(f'===== {f} =====\n{text}')
        else:
            chunks.append(text)

    result = '\n\n'.join(chunks) if sep else '\n'.join(chunks)
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as fh:
            fh.write(result)
        sys.stderr.write(f'[ok] wrote {len(files)} file(s) -> {out_path}\n')
    else:
        print(result)


if __name__ == '__main__':
    main(sys.argv)
