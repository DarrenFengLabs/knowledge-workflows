#!/usr/bin/env python3
"""check_clip.py 的回归测试。只用自造的小样例。

运行：python3 scripts/test_check_clip.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_clip import check  # noqa: E402

BG = """## 背景信息

| 项目 | 内容 |
| --- | --- |
| 原标题 | [标题](https://example.com) |
| 来源 | 测试 |
| 发布时间 | 2026-09-01 |
| 规模 | 10 分钟 · 正文约 1,000 字 |
"""


def doc(body: str, extra: str = "") -> str:
    return BG + "\n## 正文\n\n" + body + "\n\n## 补充细节\n\n- **整理说明**：测试。\n" + extra


def codes(text, **ctx):
    return {x["code"] for x in check(text, ctx)["items"] if x["level"] == "违规"}


class CheckClip(unittest.TestCase):
    def test_good_doc_passes(self):
        body = "### 开场\n\n🎙️ 你为什么做这件事？\n\n因为<u>教育是最重要的事</u>。我们做了很多年。"
        self.assertEqual(codes(doc(body), job=True, content_type="采访", has_labels=True), set())

    def test_first_line_and_h1(self):
        self.assertIn("S1", codes("# 标题\n\n" + doc("正文。")))
        self.assertIn("S2", codes(doc("# 不该有的一级标题\n\n正文。")))

    def test_background_table_required(self):
        text = doc("正文。").replace("| 规模 | 10 分钟 · 正文约 1,000 字 |\n", "")
        self.assertIn("S3", codes(text))

    def test_raw_labels(self):
        self.assertIn("U1", codes(doc("1号讲话人：你好。\n\n讲话人 2：你好。")))

    def test_bold_names(self):
        self.assertIn("P1", codes(doc("**哈希：** 这一期聊拖延。"), speaker_names=["哈希"]))
        self.assertIn("P1", codes(doc("**主持人**：这一期聊拖延。")))

    def test_bold_concept_labels_are_fine(self):
        body = "**已知的已知**：写在提示词里的内容。\n\n**已知的未知**：知道自己还没想清楚的部分。"
        self.assertNotIn("P1", codes(doc(body)))
        self.assertIn("P1", codes(doc("**章鱼**：我先说。"), speaker_names=["章鱼"]))
        self.assertIn("P1", codes(doc("**甲：** 一。\n\n**乙：** 二。\n\n**甲：** 三。\n\n**甲：** 四。")))

    def test_mic_with_name(self):
        self.assertIn("P3", codes(doc("🎙️ 主持人：你怎么看？")))
        self.assertNotIn("P3", codes(doc("🎙️ 快问一句：你怎么看？")))

    def test_loose_mic_marks(self):
        ctx = dict(has_labels=False, speaker_mode="host_only")
        res = check(doc("🎙️ 其实固执己见的人往往是在小时候缺少这样的练习。\n\n🎙️ 你怎么看？\n\n🎙️ 欢迎来到节目。"), ctx)
        loose = [x for x in res["items"] if x["code"] == "P6"]
        self.assertEqual(len(loose), 1)
        self.assertIn("1 段", loose[0]["message"])

    def test_mic_in_single_speaker(self):
        self.assertIn("P2", codes(doc("🎙️ 大家好。"), content_type="单人"))

    def test_unlabeled_source_must_not_label(self):
        self.assertIn("P4", codes(doc("说话人1：我先说。\n\n说话人2：我接着说。"), has_labels=False, speaker_mode="host_only"))
        self.assertIn("P4", codes(doc("章鱼：我先说。"), has_labels=False, speaker_mode="host_only", speaker_names=["章鱼"]))
        self.assertNotIn("P4", codes(doc("章鱼：我先说。"), has_labels=False, speaker_mode="requested", speaker_names=["章鱼"]))

    def test_lowercase_acronyms(self):
        body = "我们之前做vc，后来做ai教育，也做ceo的培训，公司快ipo了。" * 2
        self.assertIn("U5", codes(doc(body)))

    def test_bilingual_pairing(self):
        body = "<small>Hello.</small>\n\n你好。\n\n<small>Missing translation.</small>\n\n<small>Another.</small>\n\n另一段。"
        self.assertIn("F5", codes(doc(body)))

    def test_emphasis_and_tags(self):
        self.assertIn("F1", codes(doc("这是**结论。**后文")))
        self.assertIn("F2", codes(doc("这是<u>没闭合的下划线。")))

    def test_ratio_and_chunks(self):
        body = "正文" * 400
        self.assertIn("U7", codes(doc(body), mode="精修", source_chars=5000))
        self.assertIn("U9", codes(doc(body), mode="精修", chunk_ratios=[{"chunk": "002", "ratio": 0.5}]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
