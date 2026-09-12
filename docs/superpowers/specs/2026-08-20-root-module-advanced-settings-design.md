# Root 模块高级设置设计规格

## 目标

在 Root 模块本地控制台增加“高级设置”，让用户可以在自动识别失败或 ROM 行为特殊时手动指定短信数据库，同时调整读取、监听和网络重试参数。默认行为保持不变：只转发模块建立基线后收到的新短信，数据库只读打开，优先 SQLite，失败时兼容 Content Provider，并保持消息 ID 幂等。

## 配置项

新增可选字段：

```json
{
  "sms_database_path": ""
}
```

空值表示自动识别。非空值表示手动覆盖，必须是绝对路径并指向 `mmssms.db`。没有该字段的旧配置继续自动识别。

高级设置显示以下已有参数：

- `android_user_id`：要读取的 Android 用户空间，普通主用户是 `0`。
- `poll_interval_seconds`：监听器不可用时的补偿扫描间隔。
- `watcher_idle_seconds`：监听器正常时的可中断休眠时间。
- `max_backoff_seconds`：网络上传失败后的最大重试等待时间。

`initial_sync_mode` 固定为 `new_only`，控制台不提供历史短信同步开关。

## 数据库自动识别

按 Android 用户 ID 检查以下位置：

1. `/data/user/<用户ID>/com.android.providers.telephony/databases/mmssms.db`
2. 用户 ID 为 `0` 时的 `/data/data/com.android.providers.telephony/databases/mmssms.db`
3. `/data/user_de/<用户ID>/com.android.providers.telephony/databases/mmssms.db`

同一真实文件的别名路径去重。候选库只有同时满足以下条件才算可用：

- 是普通文件；
- 能以 SQLite 只读模式打开；
- 存在 `sms` 表；
- 存在 `_id`、`address`、`body`、`date`；
- 能在有界时间内查询最新 `_id`。

有短信记录的 CE 库优先于空的 DE 库，避免手机刚启动时把 Direct Boot 空库当成主库。手动路径无效时返回明确错误，不悄悄切换到其他库；用户可清空手动路径后恢复自动识别。

## 手动路径安全

- 必须是绝对路径，文件名必须为 `mmssms.db`；
- 解析符号链接后必须仍位于 `/data/user/`、`/data/user_de/` 或 TelephonyProvider 的 `/data/data` 目录；
- 验证过程只读，不创建、修改、标记已读或删除短信；
- 保存前先验证，验证失败不覆盖当前有效配置；
- 控制台和日志只显示路径、结构和错误代码，不显示短信正文、手机号、验证码或密钥。

## 控制台布局

基础设置保持简洁；新增折叠的“高级设置”，分为四组：

### 数据库

- 当前命中路径、来源（自动/手动）；
- 当前数据库检查结果、短信行数/最大 ID、识别到的 SIM 字段；
- 手动数据库路径输入框，留空表示自动识别；
- “验证数据库”按钮；
- “重新自动检测”按钮。

### 读取与唤醒

- Android 用户 ID；
- 监听空闲等待秒数；
- 补偿扫描间隔秒数。

说明文字明确：监听器收到数据库变化会立即唤醒；补偿扫描只在监听器不可用时使用。

### 网络重试

- 最大网络退避秒数。

说明文字明确：只影响上传失败，不影响新短信第一次上传。

### 运行诊断

只读显示：读取模式、有效数据库路径及来源、SQLite 检查状态、SIM 字段、最大 `_id`、本地游标、监听器状态、最近扫描/发现短信/上传时间和脱敏错误代码。

## 运行状态与兼容性

读取器单独发布诊断状态，成功的空扫描必须清除陈旧的 `reader_failed`。状态文件仍原子写入并保持 `0600`。错误只允许使用有限代码，如 `database_not_found`、`database_permission_denied`、`sms_table_missing`、`sms_columns_missing`、`provider_read_failed`。

只有持有 worker 锁的 service 进程才能清理共享 reader/watcher PID 文件；竞争进程退出时不得杀掉正在运行的 watcher。MIUI 的 `sub_id=-1` 表示未知订阅号，不应导致整行失败，仍应使用有效的 `sim_id` 做 SIM 映射。CE 与 DE 使用独立游标，SQLite 和 Provider 生成相同的幂等事件 ID。

## 验证范围

测试覆盖旧配置、CE/DE 识别、空库与有数据优先级、别名去重、合法/非法手动路径、路径穿越和符号链接逃逸、缺表/缺字段/权限错误、只读保护、`sub_id=-1`、控制台保存/验证/重新检测、诊断脱敏、监听与补偿扫描、网络退避、读取恢复、锁竞争、无历史回放、双 ABI 打包和小米 11 真机验收。
