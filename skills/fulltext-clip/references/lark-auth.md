# 飞书授权（lark-cli）

飞书妙记、飞书文档和知识库要用 lark-cli 以用户身份读取。本文说明授权能维持多久、申请哪些权限、失效或需要收窄时怎么办。命令细节以 lark-shared skill 和 `lark-cli auth <子命令> --help` 为准。

## 授权能维持多久

用户身份靠两张凭证。下表数值出自飞书开放平台《刷新 user_access_token》文档（2026-09-28 核对），文档注明都不是固定值，以接口实际返回为准：

| 对象 | 有效期 | 到期后 |
|---|---|---|
| 访问凭证 `user_access_token` | 约 2 小时（示例 7200 秒） | CLI 用刷新凭证自动换新，用户无感 |
| 刷新凭证 `refresh_token` | 示例 604800 秒，即 7 天（本机实测相同） | 每张只能用一次，换新时发一张新的；过期后只能重新授权 |
| 用户授权本身 | 365 天 | 满期后必须重新授权，续期绕不过去 |

所以“长期授权”的实际做法是：一次申请够要用的权限，然后在刷新凭证到期前至少用一次用户身份，让凭证一环接一环续下去，最长到 365 天。做不到永久免授权。长时间没有任何 `--as user` 调用时，CLI 会报 `refresh_token expired ... clearing` 并清掉本机凭证，只能重新授权。

换新后的刷新凭证是否重新计满有效期，官方文档没有明说。365 天上限的存在说明它是滚动续期，但还没实测：访问凭证过期后再用一次用户身份，对比前后的 `refreshExpiresAt` 是否后移。确认之前，不要把“用一次就再保 7 天”当作确定事实告诉用户。

## 申请哪些权限

按权限名逐项申请，不用 `--domain`。`--domain` 会连带申请整个业务域的写入和删除权限：2026-09-28 实测，7 个业务域一共授出 128 项，其中有删除云文档、删除数据表、转移文档所有权等。本 skill 只读取，清单只含读取类权限，外加 `offline_access`（没有它就拿不到刷新凭证，每 2 小时就要重新授权）和 `auth:user.id:read`，共 36 项：

```text
offline_access auth:user.id:read minutes:minutes:readonly minutes:minutes.basic:read minutes:minutes.artifacts:read minutes:minutes.media:export minutes:minutes.search:read vc:note:read vc:record:readonly vc:meeting.search:read vc:meeting.meetingevent:read docx:document:readonly docs:document.content:read docs:document.comment:read docs:document.media:download docs:document:export docs:permission.member:retrieve docs:permission.setting:read docs:secure_label:readonly wiki:node:read wiki:node:retrieve wiki:space:read wiki:space:retrieve wiki:member:retrieve drive:drive.metadata:readonly drive:file:download drive:file:view_record:readonly drive:quota_detail:read_one drive:file.meta.sec_label.read_only space:document:retrieve search:docs:read board:whiteboard:node:read sheets:spreadsheet:read sheets:spreadsheet.meta:read slides:presentation:read slides:presentation:screenshot
```

项目说明（如 AGENTS.md）要求把几个 skill 的清单合并后一次申请时，以项目说明为准。

飞书会把同一应用历次授予的权限累积起来：2026-09-28 的授权里就混着当天没申请过的表格、幻灯片权限。所以重新登录时少申请几项，并不能收回已经授出的权限。要收窄，先请用户在飞书里取消整个授权：头像 → 设置 → 账号安全中心 → 应用授权管理，找到 lark-cli 绑定的应用并取消授权（appId 见 `lark-cli config show`，应用名可用 `lark-cli api GET /open-apis/application/v6/applications/<appId> --as bot` 查到）。取消授权立即生效，之后 `auth status --verify` 报 `verify_failed`（20005），读取报 `token_expired`。这时先运行 `lark-cli auth logout` 清掉本机凭证，再按下面的流程重新授权。

## 标准流程

1. **体检。** 运行 `lark-cli auth status --json`，看 `identities.user.status`。显示 `ready` 或 `needs_refresh` 时直接干活（`needs_refresh` 会在下次调用时自动刷新）；显示 `missing`、`expired`、`verify_failed`，或调用时报缺权限，进入第 2 步。
2. **发起授权。** 按上面的清单申请：

   ```bash
   lark-cli auth login --scope "<清单>" --no-wait --json
   ```

   重新授权时也按完整清单申请，不依赖上次授权残留的权限。
3. **交给用户确认。** 把返回的 `verification_url` 原样给用户，不改编码、不加标点。lark-shared 要求同时给二维码：先进入本次 `$JOB_DIR`（单独做授权时用临时目录），再运行 `lark-cli auth qrcode "<verification_url>" --output auth-qr.png`（该命令只接受相对路径），把图片路径给用户。说明链接的有效时长（以返回的 `expires_in` 为准，实测 600 秒）。链接要放在本轮最后一条消息里：放在中途的消息里，用户可能看不到（2026-09-28 实测）。宿主支持后台命令时（如 Claude Code），发链接前先把第 4 步的命令放到后台运行：它最长轮询约 10 分钟，用户一授权就自动完成，不必等用户回复；不支持时就结束本轮，等用户回复后再执行第 4 步。链接过期而用户没确认时，不要自己反复重发（每发起一次，上一个链接就作废），告诉用户已过期，等用户说准备好了再发。
4. **完成登录。** 执行 `lark-cli auth login --device-code <device_code>`。报 `device_code is invalid` 说明链接已过期，回到第 2 步。结果若提示“以下请求 scopes 未被授予”并以非 0 退出（实测为 3），其余权限其实已经生效：按第 5 步确认，不要重试。
5. **验证并告知期限。** 运行 `lark-cli auth status --json --verify`，确认 `identities.user.status` 为 `ready`，`scope` 里的权限数与申请的一致；关键权限可用 `lark-cli auth check --scope "<权限名>"` 核对。同一节里 `refreshExpiresAt` 是刷新凭证截止时间，`expiresAt` 是访问凭证到期时间，`grantedAt` 是授权时间（加 365 天就是最晚必须重新授权的日子）。把 `refreshExpiresAt` 换算成日期告诉用户：在这之前用一次飞书功能即可续期。2026-09-28 实测：`expiresAt` 是授权后 2 小时，`refreshExpiresAt` 是 7 天后；取消旧授权后按项目清单重新授权，授予数与申请数一致，读妙记、读纪要、读模型表都正常。

## 使用中的规则

- 读用户资源的命令一律显式写 `--as user`。本机默认身份是 `auto`，用户凭证失效时会自动落到应用身份；应用身份读不到用户资源时返回空的成功结果而不报错，容易被误当成“没有内容”。
- 不为了让命令跑通而改用 `--as bot`。应用身份只能访问明确分享给应用的资源，和用户身份不是同一份权限；也不要改用网页抓取绕过授权。
- 报错含 `need_user_authorization`、`token_missing`、`token_expired`、`refresh_token_expired`、`refresh_token_revoked` 时，回到第 2 步；用户刚在飞书里取消过授权的，先 `lark-cli auth logout`。报错含 `missing_scopes` 并附 `console_url` 时，是应用后台还没开通该权限，把链接原样交给用户去开通，不要反复重新登录。
- 不在终端、日志或成品里输出任何凭证（accessToken、refreshToken、appSecret）。

## 想更省心时（需用户同意）

在本机加一个定时任务（macOS 用 launchd），每隔几天跑一次 `--as user` 的只读调用，就能在 365 天内免扫码。这会改动用户环境，只在用户明确同意后设置。设置前先实测：隔 2 小时以上再调用一次，对比前后的 `refreshExpiresAt` 是否后移，确认续期真的生效。
