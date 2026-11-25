#!/usr/bin/env python3
"""archive_to_lark.py 里纯文本处理部分的回归测试。只用自造的小样例，不连飞书。

运行：python3 scripts/test_archive_to_lark.py
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import archive_to_lark as a  # noqa: E402


def fake_resolver(ok=True):
    names = iter(f"img_{i:03d}.png" for i in range(1, 100))

    def resolve(kind, ref):
        if not ok or "missing" in ref:
            return None
        return next(names)

    return resolve


class Frontmatter(unittest.TestCase):
    def test_web_clipper(self):
        text = ('---\ntitle: 标题\nsource: https://a.com/x\nauthor:\n  - "[[刘冰雅]]"\npublished: 2025-06-20\n'
                'created: 2026-10-20\ndescription:\ntags:\n  - clippings\n---\n**正文**\n')
        meta, body = a.split_frontmatter(text)
        self.assertEqual(meta["author"], ["[[刘冰雅]]"])
        self.assertEqual(body, "**正文**\n")
        t = a.meta_table(meta)
        self.assertIn("| 原标题 | [标题](https://a.com/x) |", t)
        self.assertIn("| 作者 | 刘冰雅 |", t)
        self.assertIn("| 发布时间 | 2025-06-20 |", t)
        self.assertNotIn("clippings", t)
        self.assertNotIn("2026-10-20", t)

    def test_unfenced_header(self):
        meta, body = a.split_frontmatter("source: https://mp.weixin.qq.com/s/x\nauthor: Founder Park\nclipped: 2026-06-27\n\n正文\n")
        self.assertEqual(meta["source"], "https://mp.weixin.qq.com/s/x")
        self.assertEqual(body, "正文\n")
        self.assertIn("| 来源 | [原文链接](https://mp.weixin.qq.com/s/x) |", a.meta_table(meta))

    def test_no_frontmatter(self):
        meta, body = a.split_frontmatter("## 背景信息\n\n正文 --- 不是属性区\n")
        self.assertEqual(meta, {})
        self.assertEqual(a.meta_table(meta), "")

    def test_only_useless_fields(self):
        meta, _ = a.split_frontmatter("---\ntags:\n  - x\ncreated: 2026-01-01\n---\n正文")
        self.assertEqual(a.meta_table(meta), "")


class Transform(unittest.TestCase):
    def t(self, body, ok=True):
        return a.transform_body(body, fake_resolver(ok))

    def test_escape_unknown_tags_and_tilde(self):
        out, _ = self.t("<think> 标签，Vec<T>，a < b，~约数~，~~删除~~，<u>划线</u>")
        self.assertEqual(out, "\\<think> 标签，Vec\\<T>，a \\< b，\\~约数\\~，~~删除~~，<u>划线</u>")

    def test_english_original_to_feishu_gray(self):
        out, _ = self.t("<span style=\"color:#8f959e\">English <u>original</u>.</span>\n\n`<span style=\"color:#8f959e\">代码</span>`")
        self.assertEqual(out, '<span text-color="gray">English <u>original</u>.</span>\n\n`<span style=\"color:#8f959e\">代码</span>`')

    def test_code_untouched(self):
        body = "行内 `a<b ~x` 不动\n```python\nif a < b: x = ~y\n```\n后面 <x>"
        out, _ = self.t(body)
        self.assertEqual(out, "行内 `a<b ~x` 不动\n```python\nif a < b: x = ~y\n```\n后面 \\<x>")

    def test_link_url_untouched(self):
        out, _ = self.t("[主页](https://a.com/~user?x=<1>) 和 <https://b.com/~y>")
        self.assertEqual(out, "[主页](https://a.com/~user?x=<1>) 和 [https://b.com/~y](https://b.com/~y)")

    def test_images(self):
        body = "![Image](https://mmbiz.qpic.cn/a.png)\n![图：钢厂](https://pbs.twimg.com/x?format=jpg)\n![](x.png)\n![[Pasted image 1.png]]\n![[笔记]]"
        out, imgs = self.t(body)
        self.assertEqual(out.split("\n"), ["![](@./img_001.png)", "![图：钢厂](@./img_002.png)", "![](@./img_003.png)",
                                          "![](@./img_004.png)", "![[笔记]]"])
        self.assertEqual(len(imgs), 4)
        self.assertEqual(a.count_source_images(body), 4)

    def test_missing_images_keep_link(self):
        out, imgs = self.t("![图一](https://a.com/missing.png)\n![[missing.png]]")
        self.assertEqual(out, "[〔图片未上传〕图一](https://a.com/missing.png)\n〔图片缺失：missing.png〕")
        self.assertEqual([i["local"] for i in imgs], [None, None])

    def test_adjacent_spans_keep_space(self):
        out, _ = self.t("**加粗** *斜体* `代码` <u>线</u>")
        self.assertEqual(out, "**加粗** *斜体* `代码` <u>线</u>")
        out, _ = self.t("the **big** dog, 2 * 3 * 4")
        self.assertEqual(out, "the **big** dog, 2 * 3 * 4")


class Counting(unittest.TestCase):
    def test_words(self):
        self.assertEqual(a.count_words("中文五个字 and two words ![图](https://x.com/a.png) [链接](https://y.com)"), 5 + 3 + 2)

    def test_images_in_code_not_counted(self):
        self.assertEqual(a.count_source_images("`![a](b.png)`\n```\n![a](b.png)\n```\n<img src=\"c.png\">"), 1)


class TextCheck(unittest.TestCase):
    BODY = ("## 背景信息\n\n| 项目 | 内容 |\n| --- | --- |\n| 来源 | [链接](https://a.com) |\n\n"
            "正文 <think> 和 **加粗**。\n\n1. 有序\n- [ ] 待办\n\n```python\nprint(1)\n```\n![图](x.png)\n")

    def test_all_found(self):
        xml = ('<title>T</title><h2>背景信息</h2><table><tr><td><p>项目</p></td><td><p>内容</p></td></tr>'
               '<tr><td><p>来源</p></td><td><p><a href="https://a.com">链接</a></p></td></tr></table>'
               '<p>正文 &lt;think&gt; 和 <b>加粗</b>。</p><ol><li seq="1">有序</li></ol><checkbox done="false">待办</checkbox>'
               '<pre lang="python"><code>print(1)</code></pre><img src="tok" caption="图"/>')
        self.assertEqual(a.check_text(self.BODY, xml), [])
        self.assertEqual(a.count_feishu_images(xml), 1)
        self.assertEqual(a.feishu_title(xml), "T")

    def test_lost_tag_text_detected(self):
        xml = ('<title>T</title><p>背景信息</p><p>项目内容</p><p>来源链接</p><p>正文  和 <b>加粗</b>。</p>'
               '<p>有序</p><p>待办</p><pre><code>print(1)</code></pre>')
        self.assertEqual(a.check_text(self.BODY, xml), ["正文 <think> 和 **加粗**。"])

    def test_truncation_detected(self):
        xml = '<title>T</title><p>背景信息</p><p>项目内容</p><p>来源链接</p>'
        self.assertEqual(len(a.check_text(self.BODY, xml)), 4)


class Images(unittest.TestCase):
    def test_sniff(self):
        self.assertEqual(a.sniff_image(b"\x89PNG\r\n\x1a\n...."), "png")
        self.assertEqual(a.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "webp")
        self.assertIsNone(a.sniff_image(b"<html>forbidden</html>"))


class AuthStop(unittest.TestCase):
    """传到中途授权失效：退出码 2，照样写报告并打印路径，后面的篇标成没处理，本地一篇不删。"""

    def test_auth_failure_mid_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src, staging = tmp / "存飞书", tmp / "staging"
            src.mkdir()
            staging.mkdir()
            for name in ("a.md", "b.md"):
                (src / name).write_text("正文\n", "utf-8")
            cfg = tmp / "cfg.json"
            cfg.write_text(json.dumps({"src_dir": str(src), "staging_dir": str(staging), "space_id": "s",
                                       "short_node": "S", "long_node": "L"}), "utf-8")

            def fake_lark(args, cwd=None, timeout=600):
                if args[:2] == ["wiki", "+node-list"]:
                    return {"nodes": []}
                if args[:2] == ["docs", "+create"]:
                    raise a.LarkError("token_expired", auth=True)
                raise AssertionError(f"没预料到的调用：{args}")

            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(a, "lark", fake_lark), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = a.main(["run", "--config", str(cfg)])
            self.assertEqual(rc, 2)
            report = Path(out.getvalue().strip().splitlines()[-1])
            self.assertEqual(report.name, "report.md")
            text = report.read_text("utf-8")
            self.assertIn("失败：飞书授权问题", text)
            self.assertIn("没处理：授权失效，脚本已停", text)
            self.assertTrue((src / "a.md").exists() and (src / "b.md").exists())


if __name__ == "__main__":
    unittest.main()
