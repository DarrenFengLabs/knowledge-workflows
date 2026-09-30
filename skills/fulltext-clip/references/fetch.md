# 获取与排障

获取由 `scripts/prep.py init` 按平台自动完成，顺序是“现成文字 → 本机转写 → 给出失败码”，按成本从低到高。平台自带字幕就不重复转写；没有字幕的（多数播客、视频号、抖音、推文）直接转写。本文件在两种时候读：准备脚本报了失败码；或者需要配置登录态。

## 各平台做法

| 平台 | 元信息 | 文字（按优先顺序） | 小标题线索 | 注意 |
|---|---|---|---|---|
| 小宇宙 | 网页数据：标题、精确发布时间、时长、节目、主播、简介 | 本机转写（网页没有文字稿） | 简介里的时间轴 | 日期按北京时间；付费单集可能拿不到音频 |
| Apple Podcasts、RSS | Apple 公开查询接口 → RSS | RSS 里创作者提供的文字稿 → 本机转写 | 简介时间轴 | 查询接口只列最近 300 集 |
| YouTube | yt-dlp | 原语言人工字幕 → 自动字幕 → 本机转写 | 视频章节 | 不用机器翻译的字幕轨；“>>”只当换人线索 |
| B站 | yt-dlp | UP 主字幕 → AI 字幕 → 本机转写 | 分段章节 | 字幕要登录；排除弹幕轨 |
| X | yt-dlp | 本机转写 | — | 公开推文常可匿名下载，受保护的要 cookie 文件 |
| 抖音、小红书 | 试一次 yt-dlp | 本机转写 | — | 常失败，失败就换专用工具或请用户导出 |
| 视频号 | 无公开抓取途径 | 用户导出的文件 | — | 见下文“视频号” |
| 飞书妙记 | lark-cli | 妙记逐字稿（带说话人标签） | 妙记章节只作参考 | 以用户身份读 |
| 微信公众号 | 页面 | 正文区域原样抽取 | 原文小标题 | 只有短链接能直接抓 |
| 一般网页 | 页面元信息与结构化数据 | 正文区域抽取；抽不全时用 `prep.py select` 指定起止段 | 原文小标题 | 付费墙不绕过 |
| PDF | 文件属性 | pdftotext | 原文标题层级 | 扫描件按图片处理 |

对谈、多人播客如果有平台现成的带说话人文字稿（飞书妙记等），优先用它：本机转写不区分说话人。

## 失败码怎么处理

| 失败码 | 常见原因 | 处理 |
|---|---|---|
| NEED_LOGIN | B站字幕、YouTube 机器人验证、X 受保护内容、飞书授权失效 | 按下文“登录与授权”配置后重跑；飞书按 lark-auth.md |
| NEED_UPGRADE | yt-dlp 过旧（签名算法、接口变了） | 先看 `yt-dlp --version`，比今天早几个月就该升。升级前先向用户报一句当前版本、判断依据和要跑的命令（`brew upgrade yt-dlp` 或 `yt-dlp -U`），它动的是用户的共享环境；用户说过“直接升”就不必再问 |
| BLOCKED | 平台签名或风控；缺少模拟浏览器的依赖 | YouTube 403：挂 cookie → 装 PO Token 插件 `bgutil-ytdlp-pot-provider` → `--extractor-args "youtube:player_client=tv,web"`。抖音换 `Johnserf-Seed/f2`，小红书换 `JoeanAmier/XHS-Downloader`，或请用户导出 |
| UNSUPPORTED | 视频号；公众号长链接 | 视频号见下文；公众号请用户给短链接（`mp.weixin.qq.com/s/…`）或复制正文 |
| PAID | 付费单集 | 请用户提供音频或文字稿文件 |
| NOT_FOUND、NO_TEXT | 链接错、已下架；页面要登录或由脚本动态加载 | 核对链接；请用户复制正文或提供文件 |
| PARTIAL | 网页正文可能没抽全 | 看 `source.md` 的段号，`prep.py select --from pA --to pB` |
| TRANSCRIBE_FAILED | 转写环境或文件问题 | 见 transcribe.md |

失败时任务目录原样保留，报告里写明路径和已经拿到的东西（音频下到一半、字幕不完整等），重试往往能直接复用。

## 兜底搜索

抓不到时，找同一内容的可抓取版本或他人整理的全文：

1. 找搬运和多平台分发：搜“标题 + 作者/频道名”。视频号内容常同步到公众号或 B站。命中后换这个链接重跑准备脚本。
2. 找现成全文：搜“标题 + 全文/实录/文字稿/transcript”，打开读取正文。
3. 两轮都失败就停，向用户报告尝试过的途径、失败原因和手动方案（用户自行下载后给文件路径）。

搜索结果大量是摘要和解读，标题含“总结、要点、划重点、takeaways”的基本排除。任何候选文字都要过完整性检查：文字量不足按时长估算下限的 60% 就不能当全文。

## 登录与授权

按来源选授权方式，顺序：能匿名就匿名 → 有官方接口就走官方授权 → 只能靠网页登录的用 Firefox cookie → 都不行就请用户手动导出，或走兜底搜索。先确认平台确实要求登录，再用最小范围的登录态。

| 来源 | 方式 | 能维持多久 | 失效征兆 | 恢复 |
|---|---|---|---|---|
| 飞书妙记、飞书文档 | 官方授权（lark-cli，见 lark-auth.md） | 刷新凭证 7 天，授权最长 365 天 | `need_user_authorization`、`refresh_token expired` | 重新扫码授权 |
| YouTube | Firefox `ytdlp` 配置的 cookie；仍被拦再加 PO Token 插件 | 数周 | 要求登录、机器人验证、403 | 在 `ytdlp` 配置里重新登录 |
| B站 | 同上 | `SESSDATA` 约半年 | 提示字幕需登录 | 同上 |
| X | 单站导出的 cookies.txt，路径记在 `.local/paths.md` 的 `YTDLP_COOKIES_FILE` | `ct0` 会过期 | “Failed to parse JSON” | 重新导出 |
| 抖音、小红书 | Firefox cookie，常失败 | 不稳定 | “Fresh cookies needed”、签名错误 | 换专用工具或兜底 |
| 小宇宙 | 不需要 | — | — | — |

**禁止 `--cookies-from-browser chrome`。** 任何情况下都不要对 Chrome、Edge、Arc 等 Chromium 系浏览器用它：macOS 上要去钥匙串取“Chrome Safe Storage”密钥，会弹授权框打断用户；用户拒绝后 yt-dlp 只会静默拿到 0 个 cookie，看起来像抓取失败。那把密钥还能解密 Chrome 里所有网站的 cookie，为抓字幕不应索取这么宽的权限。Safari 需要完全磁盘访问且格式脆弱，Arc、Dia 已知失效，同样不用。

**需要登录态时一律用 Firefox 的专用配置 `ytdlp`**（cookie 是明文数据库，不碰钥匙串），并写配置目录的完整路径：`--cookies-from-browser "firefox:$FIREFOX_YTDLP_PROFILE"`（准备脚本会从 `.local/paths.md` 读取）。路径缺失时读 `~/Library/Application Support/Firefox/profiles.ini`，把 `Name=ytdlp` 那节的 `Path=` 拼在 `~/Library/Application Support/Firefox/` 后面，确认目录存在后记进 `.local/paths.md`。只写 `firefox:ytdlp` 会找不到（Firefox 建的目录带随机前缀）；只写 `firefox` 会取最近修改的配置，多个配置时容易读错。

- 一次性创建：`/Applications/Firefox.app/Contents/MacOS/firefox --no-remote -P ytdlp`，在弹出的窗口里登录 B站、YouTube、X 等后按 Cmd-Q 退出。之后抓取时保持这个 Firefox 窗口关闭。
- 这个配置登录后别拿来日常上网：打开 YouTube 标签页会轮换 cookie，给 yt-dlp 的那份随之失效。它也不要当默认配置：是不是默认，以 `about:profiles` 里“默认配置文件：是/否”为准。
- YouTube 用不要紧的小号，yt-dlp 官方文档提醒用账号抓取可能被封。
- 定时任务等无界面场景：在该配置里登录后，用浏览器扩展只导出一次 cookies.txt（首行须是 `# Netscape HTTP Cookie File`），之后 `--cookies <文件>`。
- “登录一次”现实里是数周级，平台仍可能让会话失效，届时重登或重导一次即可。

**cookie 的最小保护，每次都遵守：**

- 不打印 cookie 内容，不 `cat cookies.txt`，不贴进报告、对话或笔记；报告里只说“已挂 `ytdlp` 配置的 cookie”。
- 不把 cookie 复制进任务目录或输出目录；单独的 cookie 文件放在用户指定的私有位置并设为 `chmod 600`。
- 只为当前抓取取所需站点的 cookie，不顺手导出别的站点。
- 抓取失败时不把“挂更多 cookie、换浏览器配置、导出全量 cookie”当默认下一步：先按平台表排查，确实需要登录态再按上面的流程走，并告诉用户为什么需要。

## 飞书妙记

准备脚本以用户身份调用 lark-cli 读取妙记信息和逐字稿（`minute_token` 是妙记链接 `/minutes/` 后那一段）。逐字稿按“Speaker N 时:分:秒”分段，说话人只有编号，真名由查询代理从内容里认；飞书 AI 生成的关键词、章节、总结只作导航参考，不当原文照抄。读不到时按顺序：请妙记所有者开权限 → 请用户导出文字记录或音视频，走本地文件 → 兜底搜索。

不要用 Firefox cookie 去调飞书网页的内部接口：没有现成工具，飞书一改版就坏；飞书 cookie 等于整个飞书账号（消息、文档、审批都在里面），泄露代价远大于视频站。都走不通时，先向用户说明风险并取得同意再讨论。

## 视频号

链接无法直接下载：流是加密的、绑定微信客户端会话的短时签名地址，yt-dlp 不支持。现实路径二选一：用抓取工具 `ltaoo/wx_channels_download`（要装根证书和系统代理，先征得用户同意），或请用户手动录制、导出文件后给路径。不要在抓取上反复尝试；视频号内容常同步到公众号或 B站，可先兜底搜索。
