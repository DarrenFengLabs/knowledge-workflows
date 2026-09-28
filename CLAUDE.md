@AGENTS.md

## Claude Code 补充

- 自动压缩后，Claude Code 只重新附上每个 skill 的前 5,000 token，所以 AGENTS.md 里“压缩后重读 SKILL.md 全文”这条在这里一定要执行。
- 推理强度用 `/effort` 或 `claude --effort <level>` 设置，不写进 SKILL.md。
- 改完 skill 或本文件，可运行 `/doctor prompt-audit` 检查过时的指令、失效的路径和互相矛盾的规则。
