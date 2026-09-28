#!/usr/bin/env python3
"""check_format.py 的回归测试。用法：python3 test_check_format.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_format import analyze  # noqa: E402


def kinds(text):
    return [p[1] for p in analyze(text)['problems']]


class CheckFormatTest(unittest.TestCase):
    def test_code_block_stars_ignored(self):
        r = analyze('```\nx = 2 * 3\n```\n\n这是*正常斜体*。\n')
        self.assertEqual(r['problems'], [])
        self.assertEqual(r['italics'], 1)

    def test_inline_code_stars_ignored(self):
        r = analyze('用 `a * b` 计算，这是*重点*。\n')
        self.assertEqual(r['problems'], [])
        self.assertEqual(r['italics'], 1)

    def test_list_star_ignored(self):
        r = analyze('* 列表项\n\n这是*正常斜体*。\n')
        self.assertEqual(r['problems'], [])
        self.assertEqual(r['italics'], 1)

    def test_unclosed_bold_is_violation(self):
        self.assertEqual(kinds('**未闭合加粗\n'), ['加粗未闭合'])

    def test_unclosed_italic_is_violation(self):
        self.assertEqual(kinds('只有一个*星号\n'), ['斜体未闭合'])

    def test_bad_edges(self):
        self.assertEqual(kinds('**结论。**\n'), ['加粗失效'])
        self.assertEqual(kinds('*「重点」*\n'), ['斜体失效'])

    def test_good_edges(self):
        self.assertEqual(kinds('**结论**。*重点*，“**关键词**”\n'), [])

    def test_quote_blocks_counted_as_blocks(self):
        self.assertEqual(analyze('> 一\n> 二\n\n> 三\n')['quotes'], 2)

    def test_table_separators_with_spaces(self):
        text = '| a | b |\n| --- | :---: |\n| 1 | 2 |\n\n|x|y|\n|---|---|\n|1|2|\n'
        self.assertEqual(analyze(text)['tables'], 2)

    def test_horizontal_rule_ignored(self):
        self.assertEqual(kinds('***\n'), [])


if __name__ == '__main__':
    unittest.main()
