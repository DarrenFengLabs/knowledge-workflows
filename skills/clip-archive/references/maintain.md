# 改脚本时

改 `scripts/archive_to_lark.py` 的转义、图片或核对规则，或要端到端试跑时读本文。脚本的子命令和参数见它开头的说明。

## 单测

纯文本处理（属性区、转义、图片引用、字数、正文核对）和退出码有单测：`python3 scripts/test_archive_to_lark.py`，只用自造的小样例，不连飞书。改转义或核对规则时，先补一条能复现问题的样例，再改规则。

## 端到端试跑

传进云空间测试文件夹，本地不删：

```bash
python3 scripts/archive_to_lark.py run --test-folder <配置里的 test_folder> --item <剪藏副本> ...
```

配置里没有 `test_folder`，或飞书报这个文件夹已删除时，按缺配置处理，问用户。看结果只读 `report.md`。试跑建的文档留在测试文件夹里，按“只建不删”由用户手动删。样例副本放在哪、各测什么，见仓库 `.local/samples.md` 的 clip-archive 一节。

## 飞书导入的已知行为与脚本的对策

上传用 `lark-cli docs +create --doc-format markdown`，它把 Markdown 里的尖括号当飞书 XML 标签解析。下表是 2026-11-18 实测的行为：

| 飞书的行为 | 脚本怎么应对 |
|---|---|
| 不认识的尖括号内容（如 `<think>`、`Vec<T>`、`a < b`）被当成标签删掉 | 字面的 `<` 加反斜杠转义；常见 HTML 标签（`HTML_TAGS` 名单）按 HTML 留着 |
| 单个 `~` 变成删除线 | 单个 `~` 加反斜杠；`~~删除线~~` 不动 |
| 两个相邻格式之间的空格被吞（如 `**加粗** *斜体*`） | 换成不换行空格 |
| 飞书不支持的 HTML 标签去掉标签、保留文字，报告里出现“Unsupported tag”提示 | 不处理：文字还在，不影响核对 |
| `<span text-color="gray">` 灰色字生效，里面的加粗、下划线照常；`<span style="color:…">` 的颜色会丢 | 把 fulltext-clip 双语稿英文原文的 `<span style="color:#8f959e">` 换成飞书的灰色字（`GRAY_OPEN`）。核对只比文字，这一步失效时灰色会丢、核对照样通过，所以两边要一起改（见仓库 AGENTS.md「维护 skill」） |
| 网络图片由飞书服务器下载，推特等境外图床下载失败 | 图片全部先在本机备好，按 `@./` 本地图上传；公众号图床下载时要带来源头（Referer） |
| `$100` 这类金额 | 原样保留，不用处理 |
