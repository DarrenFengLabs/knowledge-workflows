# 远程抓取策略(模式 A:视频/播客 URL)

目标:从链接拿到**全文文字稿**。借鉴成熟工具(BibiGPT、得到/Get笔记、通义听悟、Memo.ac)的共识做法,核心是一条**「字幕优先 → 音频转写兜底 → 搜索兜底」**的路由,按成本从低到高走。

## 总原则:按成本从低到高依次尝试

1. **现成字幕/CC**(零成本、秒级,且最准)— YouTube、Bilibili 等平台原生有
2. **音频转写**(下音频 → 走 `transcribe.md`,本地 whisper.cpp)— 平台无字幕时
3. **兜底搜索**(找搬运版或他人整理的全文)
4. **明确失败**,给用户手动方案

> 经验法则(成熟工具的共识):**平台自带字幕就先抓字幕,绝不无谓重转写;没有字幕的(播客、视频号、小红书、抖音、推文)直接走音频转写。** 不要对已有字幕的视频浪费转写时间,也不要指望对无字幕视频「抓字幕」。

每拿到一份候选文本,先做文末「完整性校验」,通过才算成功。同时按文末「元信息采集」把背景/互动数据一并抓下来。

## 0. 开工准备:工作目录 + 工具版本

**① 建当次任务的工作目录**(所有下载物、字幕、元信息 JSON 都落这里,定义见 `storage.md`「中间产物」):

```bash
STAGING_DIR="$STAGING_DIR"
JOB_DIR="$STAGING_DIR/$(date +%Y-%m-%d_%H%M%S)_<内容短标识>"
mkdir -p "$JOB_DIR"
```

**绝不要**用相对路径下载(`-o "audio.%(ext)s"` 之类)——那会落在当前工作目录,而当前工作目录常常是 skill 自己的项目目录。

**② 各平台反爬/签名是动态变化的,失败时第一反应是升级 yt-dlp,而不是反复试**:

```bash
yt-dlp --version            # 版本号形如 YYYY.MM.DD,比今天早几个月就该升
```

版本确实旧了再升,**升级前先报一句**(当前版本 / 判断依据 / 要跑的命令)——`brew upgrade` 动的是用户机器上的共享环境,不是本任务的沙盒,别静默执行;用户说过"直接升"就不必再问:

```bash
brew upgrade yt-dlp || yt-dlp -U
```

(本文档不写死「当前版本是多少」——那种断言几个月就会腐坏。以 `--version` 的实际输出和今天的日期对比为准。)

## 1. 现成字幕

```bash
# 先列出可用字幕轨(逐视频确认,不同视频字幕码不同)
yt-dlp --list-subs "<URL>"

# 下载字幕(人工字幕优先),不下视频
yt-dlp --skip-download --write-subs --write-auto-subs \
  --sub-langs "zh.*,en.*,ai-zh,ai-en" --convert-subs srt \
  -o "$JOB_DIR/%(title)s.%(ext)s" "<URL>"
```

拿到 .srt/.vtt 后:去时间戳和序号,合并成连续文本。**自动字幕(auto-subs)常有逐句重叠(rolling captions),必须去重。** 字幕只是原料,之后仍走 `optimize.md` 做断句、分段、错字与专名修正。

## 2. 音频转写(无字幕时)

```bash
# 只抽音频,不下视频
yt-dlp -x --audio-format m4a --audio-quality 0 -o "$JOB_DIR/audio.%(ext)s" "<URL>"
```

拿到音频后 → **读 `transcribe.md`**(本地 whisper.cpp,全程本地不消耗额度)。中文内容务必显式指定语言;转写稿保留原始副本以便回退。

## 3. 各平台策略(2026)

### YouTube — yt-dlp 全流程最成熟
- 字幕覆盖率高,优先抓字幕(含 auto-subs)。音频:`yt-dlp -f bestaudio[ext=m4a]/bestaudio -x --audio-format m4a "<URL>"`
- **2026 新坑**:PO Token / "Sign in to confirm you're not a bot" 机器人墙、SABR 强制流。表现为 403 或 "formats have been skipped"。
  - 解法①:挂 cookies(见第 5 节登录与授权)。
  - 解法②:仍报错时装 PO-token 插件 `bgutil-ytdlp-pot-provider`(yt-dlp ≥ 2025.05),Docker/HTTP 模式跑在 `127.0.0.1:4416`,yt-dlp 会自动调用。
  - 解法③:`--extractor-args "youtube:player_client=tv,web"` 换客户端;限速/"Only images available" 多是 nsig 问题,**升级 yt-dlp** 即可。
- 注意:`ios` 客户端会忽略 cookies,需登录态时别用。

### Bilibili(B站)— yt-dlp 支持,字幕需登录
- **AI 字幕码是 `ai-zh` / `ai-en`,属真字幕轨,用 `--write-subs`(不是 auto-subs)**;UP 上传的 CC 字幕码形如 `zh-Hans`。先 `--list-subs` 看实际码。
- **字幕在 2026 基本必须登录态**(报 "Subtitles are only available when logged in");高清/高码率音频也需登录。挂 cookies:`--cookies-from-browser "firefox:$FIREFOX_YTDLP_PROFILE"`(见第 5 节)。关键 cookie 是 `SESSDATA`(约半年有效)。
- 坑:`--sub-langs` 里排除 `danmaku`(弹幕轨),否则 `--embed-subs` 会报 "Invalid data"。用 `--sub-langs "ai-zh,-danmaku"`。
- B站「AI 小助手」总结不在字幕轨,抓不到属正常,走音频转写。

### Twitter / X — 无字幕,必须登录,直接转写
- 推文视频无字幕轨 → 抽音频走转写。
- **2026 匿名访问基本关闭**,需 cookies(`auth_token` + `ct0`)。对受保护推文,**导出 `cookies.txt` 比 `--cookies-from-browser` 更稳**:`yt-dlp --cookies cookies.txt -x --audio-format m4a "<URL>"`。
- 报 "Failed to parse JSON" 多是 `ct0` 过期,重新导出 cookies;GraphQL 偶发失效 → 升级 yt-dlp。

### 小宇宙(Xiaoyuzhou)— 无 extractor,抓 og:audio 直链
- yt-dlp **无原生 extractor**,但**无需登录、无反爬**:单集页 `<head>` 里 `og:audio` 是 `media.xyzcdn.net` 的**未签名 m4a 直链**。
```bash
url=$(curl -sL "<单集页URL>" | grep -oE 'og:audio[^>]*content="[^"]+"' | sed 's/.*content="//;s/".*//')
curl -L -o "$JOB_DIR/ep.m4a" "$url"   # 已是 m4a,直接转写
```
- 找不到 `og:audio` 时,在页面 `__NEXT_DATA__` JSON 里找 `enclosure` 直链。下载后走转写。付费/会员单集可能不暴露直链〔未验证〕→ 走兜底。

### 视频号(微信)— 无公开抓取路径
- **链接无法直接下载**:流是加密的、绑定微信客户端会话的短时签名 URL。yt-dlp 不支持,headless 无解。
- 现实路径(任选):
  1. **MITM 抓取工具** `ltaoo/wx_channels_download`:装根证书+系统代理,在微信 PC 端打开该视频,工具自动捕获并解密(首 131072 字节 XOR 解密)。
  2. **请用户手动录制/导出**后把文件路径给我 → 转模式 A-本地(`transcribe.md`)。
- 不要在抓取上反复尝试,直接走上述两条之一或兜底搜索(视频号内容常同步发到公众号/B站)。

### 抖音(Douyin)— 脆弱,先试一次即走兜底
- 短视频无字幕 → 音频转写。yt-dlp 的 douyin extractor 受 `a_bogus` 签名风控影响,常报 "Fresh cookies needed"(缺 `s_v_web_id`)。
- 先试一次 `yt-dlp -x --audio-format m4a --cookies-from-browser "firefox:$FIREFOX_YTDLP_PROFILE" --impersonate chrome "<URL>"`;失败即换专用工具 `Johnserf-Seed/f2`(内部签 `a_bogus`、无水印),或走兜底。失败很常见,属预期。

### 小红书(Xiaohongshu / RED)— 最脆弱,优先专用工具
- 视频笔记无字幕 → 音频转写。`XiaoHongShuIE` 常坏(`x-s`/`x-t` 签名 + 域名迁移 `rednote.com`)。
- 先在最新 yt-dlp 上试 `--cookies-from-browser "firefox:$FIREFOX_YTDLP_PROFILE" --impersonate chrome`;坏了换 `JoeanAmier/XHS-Downloader`(活跃维护、无水印、CLI/GUI)。下载后走转写。

### 飞书妙记 — 官方接口读逐字稿,需用户授权
- yt-dlp 没有飞书的 extractor(2026.06.09 实测)。妙记自带逐字稿,相当于现成字幕,不用转写。
- 走 lark-cli 官方接口、以用户身份读取:先按 `lark-auth.md` 检查授权,再按 lark-meeting skill 的「查询妙记及其产物」取逐字稿;`minute_token` 是妙记链接 `/minutes/` 后那一段。2026-09-28 实测可用(在 `$JOB_DIR` 里运行,输出目录只接受相对路径):
  ```bash
  lark-cli minutes minutes get --params '{"minute_token":"<token>"}' --as user   # 标题、时长、创建时间(均为毫秒)、所有者
  lark-cli minutes +detail --as user --minute-tokens <token> --transcript --chapter --output-dir ./out
  ```
  逐字稿开头是录制时间与时长,接着是飞书 AI 生成的关键词,正文按「Speaker N 时:分:秒」分段。说话人只有编号,真名要从内容里认;关键词、章节、总结都是飞书 AI 产物,只作导航参考,不当原文照抄。所有者是别人的妙记,只要你有查看权限也能读(本次实测所有者不是用户本人)。
- 读不到时(没有权限、妙记属于别的组织等),按顺序:请妙记所有者开权限 → 页面允许的话请用户导出文字记录或音视频,拿文件路径走本地流程 → 第 4 节兜底搜索。
- 不用 Firefox cookie 去调飞书网页的内部接口:没有现成工具,飞书一改版就坏;而且飞书 cookie 等于整个飞书账号(消息、文档、审批都在里面),泄露代价远大于视频站。以上都走不通时,先向用户说明这些风险并取得同意,再讨论。

## 4. 兜底搜索协议(抓取失败时)

目标:找到**同一内容**的可抓取版本或他人整理的**全文**。按顺序:

1. **找搬运/多平台分发**:WebSearch 搜「标题 + 作者/频道名」。常见命中:B站搬运、YouTube 镜像、播客同步版(视频号内容尤其常同步公众号或 B站)。命中后回第 1/2 步抓取。
2. **找现成全文整理**:搜「标题 + 全文/实录/文字稿/transcript」,公众号文章常有演讲全文、访谈实录。命中后 WebFetch 抓正文。
3. 两轮都失败 → 停止,向用户报告:尝试过的途径、失败原因、手动方案(用户自行下载后给文件路径,转模式 A-本地)。

**抓取失败时 `$JOB_DIR` 原样保留、不要清理**,并在报告里给出路径和里面已有的东西(比如音频下到一半、字幕拿到了但不完整)——重试往往能直接复用,不必从头再下。清理规则见 `storage.md`「中间产物」。

**警惕**:搜索结果大量是摘要、解读、要点笔记。标题含「总结/要点/划重点/takeaways」的基本排除;一切候选文本必须过完整性校验。

## 5. 登录与授权(macOS,回答「能否记住登录不用每次登」)

按来源选授权方式,顺序:能匿名就匿名 → 有官方接口就走官方授权 → 只能靠网页登录的用 Firefox cookie → 都不行就请用户手动导出,或走第 4 节兜底搜索。

| 来源 | 方式 | 能维持多久 | 失效征兆 | 恢复 |
|---|---|---|---|---|
| 飞书妙记、飞书文档 | 官方授权(lark-cli,详见 `lark-auth.md`) | 刷新凭证官方示例 7 天,授权最长 365 天 | `need_user_authorization`、`refresh_token expired` | 重新扫码授权 |
| YouTube | Firefox `ytdlp` profile 的 cookie;仍被拦再加 PO Token 插件 | 数周级 | 要求登录、机器人验证、403 | 在 `ytdlp` profile 里重新登录 |
| B站 | 同上 | `SESSDATA` 约半年 | 提示字幕需登录 | 同上 |
| X | 单站导出的 `cookies.txt` | `ct0` 会过期 | "Failed to parse JSON" | 重新导出 |
| 抖音、小红书 | Firefox cookie + `--impersonate chrome`,常失败 | 不稳定 | "Fresh cookies needed"、签名错误 | 换专用工具或兜底 |
| 小宇宙 | 无需登录 | — | — | — |
| 视频号 | 无公开抓取路径 | — | — | 请用户手动导出 |

飞书为什么不用 Firefox cookie,见第 3 节「飞书妙记」。

### 🚫 硬规则:禁止 `--cookies-from-browser chrome`

**任何情况下都不要对 Chrome / Edge / Arc 等 Chromium 系浏览器用 `--cookies-from-browser`。** 它们在 macOS 上把 cookie 加密存放,yt-dlp 必须调 `security` 去钥匙串取「Chrome Safe Storage」密钥,这会**弹出钥匙串授权框打断用户**。钥匙串一旦自动上锁就每次都弹,而用户点「拒绝」后 yt-dlp 只会静默拿到 0 个 cookie(报 `Extracted 0 cookies (N could not be decrypted)`),看起来像抓取失败,实际是权限问题。

那把密钥能解密 Chrome 里**所有网站**的 cookie,不只是目标站点；为了抓字幕不应索取这类宽权限。

**需要登录态时,一律用 Firefox 的专用 profile `ytdlp`**(cookies 是明文 `cookies.sqlite`,不碰钥匙串、不弹窗),**并写 profile 目录的完整路径**:

```bash
yt-dlp --cookies-from-browser "firefox:$FIREFOX_YTDLP_PROFILE" "<URL>"
```

`$FIREFOX_YTDLP_PROFILE` 取自 `.local/paths.md`。缺失时读 `~/Library/Application Support/Firefox/profiles.ini`,把 `Name=ytdlp` 那节的 `Path=` 拼在 `~/Library/Application Support/Firefox/` 后面,确认目录存在后记进 `.local/paths.md`。另外两种写法不可靠(yt-dlp 2026.06.09 源码,2026-09-28 本机实测):

- `firefox:ytdlp`:yt-dlp 把名字当目录名,去找 `Profiles/ytdlp`;而 Firefox 建的目录带随机前缀(形如 `xxxxxxxx.ytdlp`),找不到 cookie 库,直接报 `could not find firefox cookies database`。
- 只写 `firefox`:yt-dlp 取所有 profile 里**最近修改**的那个 cookie 库。只有一个 profile 时碰巧对,多了日常 profile 就会读错。

Safari 需开完全磁盘访问且格式脆弱,Arc/Dia 已知失效,同样不用。

**先判断是否真的需要登录。** 先尝试匿名列字幕或下载音频；只有平台明确要求登录时才使用最小范围的登录态。英文原声的机器翻译字幕可能产生密集专名错误，应优先原文字幕或本地转写。

```bash
# ① 一次性建专用 profile(命名 ytdlp),在弹出的窗口里登录 B站/YouTube/X 等,然后 Cmd-Q 退出;
#    建好后按上文把它的目录完整路径记进 .local/paths.md
/Applications/Firefox.app/Contents/MacOS/firefox --no-remote -P ytdlp

# ② 之后所有抓取都带上它(用时保持该 Firefox 窗口关闭)
yt-dlp --cookies-from-browser "firefox:$FIREFOX_YTDLP_PROFILE" "<URL>"
```

- **关键纪律**:这个 profile 登录后**别再用它日常上网**。YouTube 会在你打开 YouTube 标签页时轮换 cookie,导致给 yt-dlp 的那份失效;闲置不动的会话最稳。
- **`ytdlp` 不要当默认 profile**:直接打开 Firefox 用的是默认 profile,`ytdlp` 是默认的话,用户偶尔开 Firefox 上网也会用到它,cookie 随之轮换。是不是默认,以 `about:profiles` 里的“默认配置文件:是/否”为准;在 `profiles.ini` 里要看 `[Install…]` 那节的 `Default=` 指向谁,`[Profile…]` 节里的 `Default=1` 是旧式标记,不作数(2026-09-28 实测:把日常 profile 设为默认后,`ytdlp` 那节仍留着 `Default=1`)。是默认的话,请用户在 `about:profiles` 新建一个日常 profile,点“设为默认配置文件”,再按 Cmd+Q 退出、重新打开 Firefox。
- **YouTube 用小号**:yt-dlp 官方文档提醒,用账号抓 YouTube 可能被临时或永久封号,建议用不要紧的小号。`ytdlp` 里的 YouTube 登录小号即可。
- **headless/定时任务**:在该 profile 里登录后,用浏览器扩展「Get cookies.txt LOCALLY」(Chrome)/「cookies.txt」(Firefox)**只导出一次**成 `cookies.txt`(macOS 用 LF 换行,首行须是 `# Netscape HTTP Cookie File`),然后 `yt-dlp --cookies ~/yt-dlp/cookies.txt "<URL>"`。
- 诚实边界:「登录一次」现实里是**数周级**,不是永久——平台仍可能服务端失效会话,届时重登/重导一次即可。失效征兆:抓取突然要登录、JSON 解析失败。

### 🔒 cookie 的最小保护(每次都遵守)

cookie 等价于用户在那个站点的登录态,拿到就能冒充他。所以:

- **绝不打印 cookie 内容**——不 `cat cookies.txt`、不把它贴进报告或对话、不写进笔记正文。报告里只说「已挂 `ytdlp` profile 的 cookies」这种事实,不带值。
- **绝不把 cookies 复制进 `$JOB_DIR` 或 `$OUTPUT_DIR`**。cookie 不应进入任务产物、同步目录或仓库；若使用单独文件，保存在用户指定的私有位置并设为 `chmod 600`。
- **只为当前这次抓取取所需站点的 cookie**,不顺手导出别的站点;`--cookies-from-browser chrome` 的禁令(上面那条)本质也是这个理由。
- 抓取失败时**不要**把「挂上更多 cookie / 换个浏览器profile / 导出全量 cookies」当作默认下一步——先按各平台策略排查,确实需要登录态再按上面的流程走,并告诉用户为什么需要。

## 完整性校验(强制,任何来源的文本都要过)

1. 获取内容时长(`yt-dlp --print duration "<URL>"`,或页面信息,或问用户)
2. 估算期望字数:中文 200–280 字/分钟;英文 130–170 词/分钟
3. 候选文本 < 期望下限的 60% → 判定为摘要/节选,**拒绝采用**,继续找
4. 无法获取时长时:向用户报告候选文本字数和来源,请用户判断
5. 校验通过后,在「背景信息」里记录:抓取途径 + 校验结果(如「12,400 字 / 52 分钟,符合全文预期」)

## 元信息采集(供「背景信息 / 补充细节」)

抓取时顺手收集,写入文字稿首尾(格式见 `optimize.md`):

```bash
# 标题、上传者、发布日期、时长、点赞/评论等
yt-dlp --print "%(title)s | %(uploader)s | %(upload_date>%Y-%m-%d)s | %(duration)ss | 赞%(like_count)s 评%(comment_count)s" "<URL>"
# 或导出完整元信息 JSON 供挑选
yt-dlp --skip-download --write-info-json -o "$JOB_DIR/meta.%(ext)s" "<URL>"
```

- **背景信息(开头)**:发布时间、来源/作者、时长字数、抓取途径。讨论背景若内容里说清了可一句话概括,**说不清就不要编**。
- **补充细节(结尾)**:互动数据(点赞/转发/评论/收藏)、视觉元素(封面、关键插图、画面里的图表)。互动数据直接来自上面的 `like_count` 等字段;数字拿不准标〔?〕。
