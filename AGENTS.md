# Knowledge Workflows 项目路由

本仓库包含四个职责互斥、可以独立安装的 skill。根据用户的输入与目标自动选择，不要求用户显式写 `$skill-name`。

- 单篇或少量外部文章、视频、播客、截图、短摘录，需要忠实转换成易读 Markdown：使用 `fulltext-clip`。
- 用户亲自参与的课堂、会议或讲座材料，需要融合 ASR、手写、截图、照片、PPT/PDF：使用 `notes-with-media`。
- 超过 5 条图文链接、聚合页、收藏夹、日报/周报，或用户要求分类、筛选、排优先级、按时间范围综述：使用 `reading-triage`。
- 用户要求“更新模型表”“入表”“维护模型/工具记录”“归档模型”等结构化维护动作：使用 `model-table`。

边界规则：

1. 裸链接默认交给 `fulltext-clip`；若抓取后发现是含 5 条以上内容的聚合页，切换到 `reading-triage` 并告知用户。
2. 多个视频/音频仍逐个走 `fulltext-clip`，不要仅因数量多就做阅读分诊。
3. `fulltext-clip` 和 `reading-triage` 发现“明确型号或工具名 + 可复核硬锚点”时，只在产出末尾附“拟入表”提议；未获确认不得写表。
4. `model-table` 只有在用户确认后才能执行“备份 → 写入 → 回读校验”。
5. `.local/` 是项目私有配置，必须保持 Git 忽略；缺少所需配置时询问用户，不猜路径、ID 或凭据。

## 长任务与批量任务

- 用户要求一口气处理一批（多单元笔记、整批分诊、多条入表）时，先把完成条件写进进度文件，做完一篇接着做下一篇。只在这些情形停下来问用户：缺输入或路径、未归位媒体待认领、拟入表或写表待确认、自己无法解决的失败。
- 上下文被压缩后，或拿不准规则时，开下一篇前重读对应 `skills/<name>/SKILL.md` 全文，不凭记忆续写。

## 维护 skill

- 只改 `skills/` 下的正本；`.agents/skills/` 与 `.claude/skills/` 是指向正本的符号链接。
- SKILL.md frontmatter 只用 Agent Skills 通用字段（`name`、`description`，必要时 `license`、`compatibility`、`metadata`、`allowed-tools`）。某个智能体的专属字段不写进正本：只认通用字段的渠道（如上传到 claude.ai 或 Skills API）会报错。
- 规则写清目标、约束和理由；少用堆叠的粗体和“必须/绝不”，强调留给确实反复出错的规则。事实纪律、写表确认与备份回读是交付标准，任何时候都不删。
- SKILL.md 保持在 500 行内；任何时候都适用的规则放在前面，细节放 `references/`。
- 改完用真实样例回归；`notes-with-media` 用 `scripts/check_format.py` 对比改前改后的违规数与斜体、金句、表格数。
