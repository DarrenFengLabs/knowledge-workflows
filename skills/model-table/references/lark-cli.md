# lark-cli 操作注意

开工先运行 `lark-cli --version` 与 `lark-cli auth status`。CLI 行为可能随版本变化；升级会改动用户环境，执行前先说明原因与命令。

## 参数名与返回结构（2026-09-03 新踩）

- **`--base-token` 不是 `--app-token`。** 用错会报 `unknown flag`，好在它会给 `did you mean` 提示
- **返回结构不是 `data.items`**：
  - `+table-list` → `data.tables[]`，元素键是 `id` / `name` / `records_count`
  - `+field-list` → `data.fields[]`，元素键是 **`name`** 而不是 `field_name`
- 每次调用都会带一个 `_notice` 块（版本更新提示），解析时忽略它

## 字段与视图（2026-09-02 实测）

- `+view-set-filter` 的 payload 是 `{"logic":"and","conditions":[[字段,操作符,值]]}`，**不要外包 `{"filter":...}`**；空值判断用 `isNotEmpty`
- `+view-create` 用 `{"name":..,"type":"grid"}`，不是 `view_name` / `view_type`
- **关联字段类型是 `link`**（不是 single_link / duplex_link），目标表键名是 **`link_table`**（不是 table_id / property），**不接受 `multiple` 键**
- **`bidirectional` 只能在创建时指定，`+field-update` 改不了**（报错明示）。要双向就一次建对，否则只能删了重建
- **`--dry-run` 不做深度校验**（三种错误的 type 都返回 OK），**不能用它验证字段类型**

## 写入（2026-07 实测，CLI 版本可能已变，以实测为准）

- `+record-batch-create` 单批最多 200 行；`--json` 里 fields 的顺序对应 rows 二维数组，空格填 `null`
- `--json @文件` 只认当前目录下的相对路径；文件在别处时用 `--json "$(cat 路径)"` 内联
- `+record-upsert --record-id <id>` 的 payload 直接写字段，不要外包 `fields`
- `+record-delete` 要加 `--yes`；`+field-create` 没有 `--yes`
- 单元格格式：多选是字符串数组，日期是 `"YYYY-MM-DD HH:mm:ss"`，数字用 JSON number，链接传字符串
- 多选写入表里没有的选项会报 `not_found`（单选未实测）：先 `+field-get` 拿现有选项，追加后用 `+field-update` 整体写回。这是覆盖式更新，漏掉的旧选项会被删掉

## 认证

写表需要 user 身份。授权的有效期、登录、续期和失效处理见 [lark-auth.md](lark-auth.md)；user 身份显示 `needs_refresh` 是正常的，下次调用会自动刷新。

## 备份怎么做

写入前导出到 **`$STAGING_DIR/base备份/base-backup-YYYYMMDD/`**，JSON + CSV 各一份；任务涉及的表都要导出。

⛔ **不要写进 `$OUTPUT_DIR`**。数据库转储是中间产物，可能包含结构化内部数据，只能进入用户配置的本地暂存目录。

已验证的导出注意：`+record-list` 的 `--limit` **上限是 200**（`--format json` 时），填 500 会直接报 `must be between 1 and 200` 而不是静默截断；`--format json` 返回矩阵结构（`data.fields` 列名数组 + `data.data` 行数组 + `data.record_id_list`），不是对象数组，转 CSV 时需按列名拼接。若新版 CLI 行为不同，以实测输出为准并更新本文档。
