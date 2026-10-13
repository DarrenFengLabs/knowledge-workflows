@AGENTS.md

## Claude Code 补充

- 自动压缩后，Claude Code 只重新附上每个 skill 的前 5,000 token，所以 AGENTS.md 里“压缩后重读 SKILL.md 全文”这条在这里一定要执行。
- 推理强度用 `/effort` 或 `claude --effort <level>` 设置，不写进 SKILL.md。
- 改完 skill 或本文件，可运行 `/doctor prompt-audit` 检查过时的指令、失效的路径和互相矛盾的规则。
- 剪藏的子代理角色定义在 `.claude/agents/`（`clip-solo`、`clip-survey`、`clip-writer`、`clip-finish`），模型档位写在各自的 `model` 与 `effort` 字段（一篇做到底的 `clip-solo` 与写作角色为中等推理强度，查询与收尾沿用主对话），`disallowedTools` 关掉剪藏用不上的工具，减少每个代理开工时的固定上下文；换档前按 AGENTS.md「模型档位」先用样例对比。新建或修改角色定义后，要过一会儿或重开会话才会出现在可派的角色里；还没出现时用通用子代理，把 dispatch.md 里的提示原样发过去。
