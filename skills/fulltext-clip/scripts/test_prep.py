#!/usr/bin/env python3
"""准备与拼装脚本的回归测试。只用自造的小样例，不联网。

运行：python3 scripts/test_prep.py
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cliplib import htmltext  # noqa: E402
from cliplib.analyze import compute_stats, plan_chunks  # noqa: E402
from cliplib.common import beijing_date, read_segments  # noqa: E402
from cliplib.platforms import chapters_from_text, detect  # noqa: E402
from cliplib.transcript import (  # noqa: E402
    cues_to_segments, merge_same_speaker, normalize_labels, parse_labeled_lines, parse_subtitles,
)


def run(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True)


class Parsers(unittest.TestCase):
    def test_rolling_vtt_dedupe(self):
        vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
hello everyone

00:00:02.000 --> 00:00:04.000
hello everyone
welcome to the show

00:00:04.000 --> 00:00:06.000
welcome to the show
>> thanks for having me
"""
        cues = parse_subtitles(vtt)
        self.assertEqual([c["text"] for c in cues], ["hello everyone", "welcome to the show", ">> thanks for having me"])
        segs = cues_to_segments(cues, "en")
        self.assertTrue(segs[-1]["turn"])
        self.assertEqual(segs[-1]["text"], "thanks for having me")

    def test_baidu_style_glued_timestamp(self):
        lines = ["某节目标题", "1号讲话人  00:00:06我是主持人。", "2号讲话人  00:00:5041岁。", "1号讲话人  01:02:03好的。"]
        segs, info = parse_labeled_lines(lines)
        self.assertEqual(info["title"], "某节目标题")
        self.assertEqual(segs[1]["text"], "41岁。")
        self.assertEqual(segs[1]["ts"], "00:00:50")
        self.assertEqual(segs[2]["ts"], "01:02:03")
        mapping = normalize_labels(segs)
        self.assertEqual(mapping, {"L1": "1号讲话人", "L2": "2号讲话人"})

    def test_feishu_style_name_line(self):
        lines = ["Speaker 1 00:00:05", "大家好，", "今天聊聊。", "张三 00:01:02", "我来补充。"]
        segs, info = parse_labeled_lines(lines)
        self.assertEqual([s["label"] for s in segs], ["Speaker 1", "张三"])
        self.assertEqual(segs[0]["text"], "大家好，今天聊聊。")
        self.assertGreaterEqual(info["matched"], 0.4)

    def test_merge_same_speaker(self):
        segs = [{"ts": "00:00:01", "label": "L1", "text": "一。"}, {"ts": "00:00:02", "label": "L1", "text": "二。"},
                {"ts": "00:00:03", "label": "L2", "text": "三。"}]
        self.assertEqual([s["text"] for s in merge_same_speaker(segs, "zh")], ["一。二。", "三。"])

    def test_chapters_from_shownotes(self):
        text = "【收听攻略】\n- **01:23** 线下课程预告：赤足训练（9/12-13上海、10/17-18深圳）、灵活与稳定课程安排，另有很多很多说明文字" \
               "\n- **01:59** 正式开场\n- **02:28** 真实近况\n不是时间轴的一行"
        ch = chapters_from_text(text, 600)
        self.assertEqual([c["start_s"] for c in ch], [83, 119, 148])
        self.assertTrue(ch[0]["title"].startswith("线下课程预告"))

    def test_beijing_date(self):
        self.assertEqual(beijing_date("2026-08-30T16:00:00.000Z"), "2026-08-31")
        self.assertEqual(beijing_date("20260908"), "2026-09-08")
        self.assertEqual(beijing_date(1788000000000), beijing_date(1788000000))

    def test_detect(self):
        cases = {
            "https://www.xiaoyuzhoufm.com/episode/abc": "xiaoyuzhou",
            "https://podcasts.apple.com/nz/podcast/id1?i=2": "apple",
            "https://www.youtube.com/watch?v=x": "youtube",
            "https://www.bilibili.com/video/BV1": "bilibili",
            "https://x.com/a/status/1": "x",
            "https://mp.weixin.qq.com/s/abc": "wechat",
            "https://abc.feishu.cn/minutes/obcn1": "feishu_minutes",
            "https://example.com/a.pdf": "pdf",
            "https://example.substack.com/p/x": "web",
        }
        for url, plat in cases.items():
            self.assertEqual(detect(url)["platform"], plat, url)


class Html(unittest.TestCase):
    def test_fragment_blocks(self):
        html = ("<p><span>第一段。</span></p><h2>小标题</h2><p><strong>结论。</strong>后文</p>"
                "<ul><li>甲</li><li>乙</li></ul><table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>")
        blocks = htmltext.to_blocks(htmltext.parse(html))
        self.assertEqual(blocks[0], "第一段。")
        self.assertEqual(blocks[1], "### 小标题")
        self.assertEqual(blocks[2], "**结论**。后文")
        self.assertIn("- 甲", blocks)
        self.assertTrue(any(b.startswith("| a | b |") for b in blocks))


class Chunking(unittest.TestCase):
    def test_prefers_speaker_change(self):
        items = [{"text": "字" * 300 + "。", "ts": None, "label": "L1" if (i // 7) % 2 else "L2"} for i in range(120)]
        plan = plan_chunks(items, 10000)
        for a, _ in plan[1:]:
            self.assertNotEqual(items[a]["label"], items[a - 1]["label"])

    def test_no_tiny_tail(self):
        items = [{"text": "字" * 1000, "ts": None, "label": None} for _ in range(21)]
        plan = plan_chunks(items, 10000)
        last = plan[-1]
        self.assertGreaterEqual(sum(len(items[i]["text"]) for i in range(*last)), 3000)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_interview_transcript_flow(self):
        src = self.tmp / "访谈.txt"
        lines = ["一期访谈"]
        for i in range(40):
            lines.append(f"1号讲话人  00:{i:02d}:00你怎么看这个问题？")
            lines.append(f"2号讲话人  00:{i:02d}:10" + "我觉得这件事要分两层看，" * 20)
        src.write_text("\n".join(lines), encoding="utf-8")
        job = self.tmp / "job"
        r = run(HERE / "prep.py", "init", "--input", src, "--job-dir", job, "--chunk-chars", "3000")
        self.assertEqual(r.returncode, 0, r.stderr)
        stats = json.loads((job / "stats.json").read_text(encoding="utf-8"))
        self.assertTrue(stats["has_labels"])
        self.assertLess(stats["labels"]["L1"]["share"], 0.2)
        self.assertGreater(len(stats["chunk_plan"]), 1)
        sheet = {"content_type": "采访", "mode": "精修", "speaker_mode": "labels", "title": "一期访谈",
                 "title_zh": "一期访谈", "published": "2026-09-01", "source_line": "测试", "url": "",
                 "speakers_line": "主持 甲；嘉宾 乙。正文里 🎙️ 开头的段落是主持人的话",
                 "speakers": [{"label": "L1", "name": "甲", "role": "host", "confirmed": True},
                              {"label": "L2", "name": "乙", "role": "guest", "confirmed": True}]}
        (job / "sheet.json").write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
        r = run(HERE / "prep.py", "apply-sheet", "--job-dir", job)
        self.assertEqual(r.returncode, 0, r.stderr)
        segs = read_segments(job / "chunks" / "001.md")
        self.assertEqual({s["label"] for s in segs}, {"🎙️", None})
        tasks = json.loads((job / "tasks.json").read_text(encoding="utf-8"))
        self.assertTrue(all(t["kind"] == "精修" for t in tasks))

    def test_clean_article_flow(self):
        src = self.tmp / "文章.md"
        paras = ["## 第一部分"] + ["这是一段干净的正文，有标点，也没有口头禅。" * 5 for _ in range(12)]
        src.write_text("\n\n".join(paras), encoding="utf-8")
        job = self.tmp / "job2"
        r = run(HERE / "prep.py", "init", "--input", src, "--job-dir", job)
        self.assertEqual(r.returncode, 0, r.stderr)
        stats = json.loads((job / "stats.json").read_text(encoding="utf-8"))
        self.assertEqual(stats["suggested_mode"], "整理")
        sheet = {"content_type": "文章", "mode": "整理", "speaker_mode": "none", "title": "文章",
                 "title_zh": "文章", "published": "2026-09-01", "source_line": "测试来源", "url": "https://example.com/a"}
        (job / "sheet.json").write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(run(HERE / "prep.py", "apply-sheet", "--job-dir", job).returncode, 0)
        self.assertTrue((job / "out" / "001.md").exists())
        (job / "tail.md").write_text("## 补充细节\n\n- **整理说明**：测试。\n", encoding="utf-8")
        r = run(HERE / "assemble.py", "build", "--job-dir", job)
        self.assertEqual(r.returncode, 0, r.stderr)
        final = (job / "final.md").read_text(encoding="utf-8")
        self.assertTrue(final.startswith("## 背景信息\n\n| 项目 | 内容 |"))
        self.assertIn("| 原标题 | [文章](https://example.com/a) |", final)
        self.assertIn("## 正文", final)

    def test_translation_pairing(self):
        job = self.tmp / "job3"
        (job / "chunks").mkdir(parents=True)
        (job / "out").mkdir()
        (job / "chunks" / "001.md").write_text("〔块说明〕x\n\n〔p1〕Hello world.\n\n〔p2〕Second one.\n", encoding="utf-8")
        (job / "out" / "001.zh.md").write_text("### 开场 / Opening\n\n〔p1〕你好，世界。\n\n〔p2〕🎙️ 第二段。\n", encoding="utf-8")
        (job / "tasks.json").write_text(json.dumps([{"chunk": "001", "input": "chunks/001.md", "output": "out/001.zh.md",
                                                     "notes": "notes/001.json", "kind": "翻译-整理"}]), encoding="utf-8")
        (job / "meta.json").write_text("{}", encoding="utf-8")
        (job / "sheet.json").write_text(json.dumps({"title": "T", "mode": "翻译"}), encoding="utf-8")
        r = run(HERE / "assemble.py", "build", "--job-dir", job)
        self.assertEqual(r.returncode, 0, r.stderr)
        final = (job / "final.md").read_text(encoding="utf-8")
        self.assertIn("<small>Hello world.</small>\n\n你好，世界。", final)
        self.assertIn("<small>Second one.</small>\n\n🎙️ 第二段。", final)


class Stats(unittest.TestCase):
    def test_lower_latin_and_mode(self):
        items = [{"text": "我们做的是sel社会情感学习，之前在vc行业，做ai相关。", "label": None, "ts": None}] * 30
        st = compute_stats(items, "platform_transcript", "zh", {})
        self.assertIn("sel", st["lower_latin"])
        self.assertEqual(st["suggested_mode"], "精修")


if __name__ == "__main__":
    unittest.main(verbosity=1)
