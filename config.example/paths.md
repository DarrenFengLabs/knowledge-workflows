# 路径与私有资源 ID 模板

> 克隆后把 `config.example/` 复制为 `.local/`，只在 `.local/paths.md` 填写真实值。`.local/` 已被 Git 忽略。

## 目录

| 变量 | 值 |
|---|---|
| `OUTPUT_DIR` | `<最终 Markdown 输出目录的绝对路径>` |
| `STAGING_DIR` | `<中间产物暂存目录的绝对路径>` |
| `WHISPER_MODEL` | `<whisper.cpp 模型文件的绝对路径>` |
| `FIREFOX_YTDLP_PROFILE` | `<可选：Firefox 专用配置 ytdlp 的目录完整路径，B站字幕等需要登录时用>` |
| `YTDLP_COOKIES_FILE` | `<可选：X 等单站导出的 cookies.txt 的私有位置，权限 600>` |

确认 `$STAGING_DIR` 是否会被云盘同步；大型媒体、字幕与数据库备份通常应放在不自动同步的目录。路径可能含空格，shell 中始终加双引号。

## 模型表（仅 model-table 需要）

| 项 | 值 |
|---|---|
| Base token | `<base_token>` |
| AI 模型表 | `<table_id>` |
| AI 工具表 | `<table_id>` |
| 模型评测表 | `<table_id>` |
| 相关视图 | `<view_id>` |
| 相关字段 | `<field_id>` |

不要在这里保存 API key、用户 access token 或 cookie；使用受保护的环境变量或客户端认证存储。

