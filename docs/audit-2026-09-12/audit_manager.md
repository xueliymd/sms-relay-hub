# 管理端 App 审计报告 — `io.smsrelay.manager` v1.0.8 (versionCode 9)

只读审查，未修改任何文件。对照后端 `backend/app/api/relay.py`、`updates.py` 与 `services/store.py` 逐条核对。

## 结论速览（先答三个核心问题）
- **接口路径/字段是否与后端一致**：一致。全部读写接口的 path、method、请求/响应字段名已逐条比对，未发现不一致（详见下）。
- **脱敏值 bug 是否真的修干净**：已修。SIM 编辑框不再预填脱敏号码（`MainActivity.showSimEditDialog`），令牌/设备 ID 也无"掩码当真实值"回传。
- **远程控制 UI 是否存在**：**不存在**。后端已有 `/admin/devices/{id}/commands` 等指令接口，但管理端 `RelayAdminClient` 没有任何 command 方法，UI 也无下发/查看入口，`ApiScreen` 仅把轮询地址当信息展示。与历史记录"未做"一致。

---

## 一、确认已正确实现（逐条核对）

### 1. 接口路径/方法/字段 与后端完全对齐
管理端 `RelayAdminClient.kt` 调用的全部端点均已核对：

| 操作 | 管理端调用 | 后端路由 (`relay.py`/`updates.py`) | 结论 |
|---|---|---|---|
| 健康检查 | `GET /api/v1/health` | `health.py` `@router.get("/health")` 返回 `{status:"ok"}` | ✓ |
| 登录 | `POST /api/v1/admin/login` | `@router.post("/admin/login")` | ✓ |
| 运行时 | `GET /api/v1/admin/runtime` | `@router.get("/admin/runtime")` | ✓ 字段全对 |
| 设备列表 | `GET /api/v1/admin/devices` | `@router.get("/admin/devices")` | ✓ |
| SIM 列表 | `GET /api/v1/admin/devices/{id}/sim-profiles` | `@router.get("/admin/devices/{device_id}/sim-profiles")` | ✓ |
| 短信列表 | `GET /api/v1/admin/messages` (limit/device_id/sim_slot/created_after/after_id/offset) | `@router.get("/admin/messages")` | ✓ 查询参数一致 |
| 版本历史 | `GET /api/v1/releases/{platform}/history?channel=stable` | `updates.py` `@router.get("/{platform}/history")` | ✓ |
| 创建设备 | `POST /api/v1/admin/devices` `{name,client_type,auth_kind}` | `@router.post("/admin/devices")` | ✓ |
| 轮换凭据 | `POST /api/v1/admin/devices/{id}/rotate-credential` | `@router.post("/admin/devices/{device_id}/rotate-credential")` | ✓ |
| 恢复设备 | `POST /api/v1/admin/devices/{id}/restore` | `@router.post("/admin/devices/{device_id}/restore")` | ✓ |
| 撤销设备 | `DELETE /api/v1/admin/devices/{id}` | `@router.delete("/admin/devices/{device_id}")` | ✓ |
| 永久删除 | `DELETE /api/v1/admin/devices/{id}/purge` | `@router.delete("/admin/devices/{device_id}/purge")` | ✓ |
| SIM 改/删 | `PUT`/`DELETE /api/v1/admin/devices/{id}/sim-profiles/{slot}` | `@router.put`/`@router.delete(.../sim-profiles/{slot})` | ✓ body `{phone_number,source}` |
| 已读/未读 | `POST /api/v1/admin/messages/{id}/read\|unread` | `@router.post("/admin/messages/{message_id}/read")`/`/unread` | ✓ 读 `read_at` |
| 删单条 | `DELETE /api/v1/admin/messages/{id}` | `@router.delete("/admin/messages/{message_id}")` | ✓ |
| 批量删除 | `POST /api/v1/admin/messages/bulk-delete` `{message_ids:[...]}` | `@router.post("/admin/messages/bulk-delete")` | ✓ 返回 `deleted` |
| 保留清理 | `POST /api/v1/admin/retention/run` | `@router.post("/admin/retention/run")` | ✓ 返回 `redacted_messages` |
| 改管理员 | `PUT /api/v1/admin/account` `{current_password,username?,password?}` | `@router.put("/admin/account")` | ✓ 返回新 session |
| 建令牌 | `POST /api/v1/admin/api-clients` | `@router.post("/admin/api-clients")` | ✓ 返回 `token` |
| 列令牌 | `GET /api/v1/admin/api-clients` | `@router.get("/admin/api-clients")` | ✓ `{id,token,active,created_at}` |

- **`DEVICE_AUTH_KINDS` (RelayAdminClient.kt:304) 与后端 `SUPPORTED_DEVICE_CONTRACTS` (store.py:54-67) 完全对齐**：android_app→hmac、root_module→hmac、ios_shortcut→bearer、ve_tweak→hmac、air724_mqtt→hmac、air724→script_api。管理端提供的 6 种设备类型后端均支持，创建不会因类型不匹配失败。

### 2. 设备写操作（声称 #1）
- 创建/轮换/撤销/恢复/永久删除均有二次确认（`MainActivity.confirm` / 各 `xxxDevice` 方法），创建/轮换/恢复后弹出"仅显示一次"凭据对话框（`showCredentialDialog`）。✓
- 危险操作"永久删除"有显式"不可恢复"警告文案。✓ 无防误触空白。

### 3. SIM 手机号脱敏（声称 #2）
- `MainActivity.showSimEditDialog` (MainActivity.kt:687-713)：输入框 `hint` 提示输入完整号码，**明确不预填** `sim.phoneNumberMasked`，仅把脱敏值显示在说明文字里。历史 bug 已修。✓
- 没有发现令牌、设备 ID 被当作真实值回传/提交的同类问题。

### 4. 消息操作（声称 #3）
- 已读/未读、单条删除、批量删除、保留清理均已接线且回调非空。✓
- **批量删除确认文案已改为准确**：`MainActivity.bulkDeleteMessages` (742-755) 文案为"将删除当前列表已加载的全部 N 条短信"，与实际行为（删除 `timeline.items()` 全部已加载）相符。原"文案与实际行为不符"的缺陷已修正。✓

### 5. 管理员改密 / 脚本令牌（声称 #4）
- 改密弹窗强制要求"当前密码"，调 `updateAdminAccount`；成功后 `logout()` 重新登录。✓
- 脚本令牌仅有"新建"与"刷新列表"入口，**没有"撤销"按钮**（后端 `relay.py` 也无撤销端点），与需求一致。✓

### 6. 接口页地址（声称 #5）
`ApiScreen.kt:19-28` 全部地址正确：
- 短信上报 = `/api/v1/ingest/messages` ✓（此前不存在的 `/api/v1/ingest` 已不存在，全仓检索确认）
- 设备指令轮询 = `/api/v1/devices/{device_id}/commands/pending` ✓（对应 `relay.py:1037`）
- 快捷指令 `/ingest/shortcut`、MQTT `/ingest/mqtt`、管理员消息 `/admin/messages`、WS `/ws/admin/messages`、`/ws/otp`、`/otp/claim` 均正确。无笔误路径。✓

### 7. 实时消息 / 时间 / 版本（声称 #7）
- `AdminMessageSocket` 连 `/api/v1/ws/admin/messages`，auth→subscribe→心跳，断线退避重连。✓
- `MessageTime` 优先用 `message.messageDate`（设备时间）回退 `receivedAt`。✓
- `MessageTimeline` 按 createdAt 降序合并/排序，`cursor()` 用于补拉。✓
- `ReleasePolicy.updateAvailable` 按 `versionCode` 比较，面板用 `releases["manager"]` 对照 `BuildConfig.VERSION_CODE`。✓

### 8. 连接失败诊断（声称 #8）
`MainActivity.errorText` (872-879) 区分 `Unauthorized/NetworkFailure/HttpFailure/InvalidResponse`，显示**请求地址**、**HTTP 状态码**、**系统原因**（NetworkFailure 带 `reason`）。1.0.2 特性仍在，无回退。✓

---

## 二、发现的问题

| # | 文件:行号 | 严重度 | 问题 |
|---|---|---|---|
| 1 | `RelayAdminClient.kt`（全书无 command 方法）；`ApiScreen.kt:24`；`DeviceScreen.kt`（无指令按钮） | 中 | **远程控制 UI 完全未做**：后端已提供 `POST /admin/devices/{id}/commands`、`GET /admin/devices/{id}/commands`、`GET /devices/{id}/commands/pending`（relay.py:996-1058），但管理端无任何调用与界面，仅把轮询地址当文本展示。属"未接线功能"。 |
| 2 | `RelayAdminClient.kt:209-214` + `MainActivity.kt:742-755` | 中 | **批量删除 >500 条会直接失败**：UI 文案承诺"删除当前列表已加载的全部 N 条"，`bulkDeleteMessages` 取 `timeline.items()`（时间线容量 5000，可滚动累积远超 500）；而 `RelayAdminClient.bulkDeleteMessages` 有 `require(size <= 500)`，超过即抛 `IllegalArgumentException` 导致操作失败。当已加载 >500 条时批量删除不可用。 |
| 3 | 后端 `relay.py:662-687`（`_serialize_message` 缺 `otp_code`）；管理端 `RelayAdminClient.kt:339` 读 `otp_code` | 中 | **REST 拉取的短信不含验证码**：后端仅在 `_publish_admin_message` (690-696) 给 WebSocket 事件附加 `otp_code`，REST `list_messages` 走 `_serialize_message` 不输出该字段。结果：实时推送的短信显示"验证码"按钮，而经 HTTP 初次/补拉的历史短信 `otp_code` 为 null、不显示按钮。属后端序列化不一致，管理端无法单独修复。 |
| 4 | `MainActivity.kt:423` `openRelease` | 低 | 下载地址拼接 `baseUrl.http(release.downloadUrl)` 依赖后端返回**相对路径**；若 `release_catalog` 返回绝对 URL 会被重复拼接成无效地址。需确认目录返回格式。 |
| 5 | `RelayAdminClient.kt:59` 读 `httpsEnabled`；后端 `relay.py:879` | 低 | 后端 `https_enabled` 以 `public_base_url` 是否以 `https://` 开头推算，不反映实际 TLS 服务状态；管理端照实展示"HTTPS：已启用/未启用"可能误导。后端语义问题，管理端如实呈现。 |

> 额外观察（非缺陷）：包简介称"Jetpack Compose"，但源码实际为传统 Android View（`LinearLayout`/`ScrollView`/`AlertDialog`）实现，与描述不符，不影响功能。

---

## 三、无法确认的疑点
- **下载地址格式**（#4）：受限于无法运行后端，未确认 `release_catalog` 的 `download_url` 是相对路径还是绝对 URL，故不能判定 `openRelease` 一定出错。
- **指令能力范围**：已确认管理端未接任何设备指令接口；后端指令类型白名单（`store.py` DEVICE_COMMAND_TYPES：query_battery/query_recent_sms/trigger_upload）目前也仅设备侧消费，管理端缺失属预期。
- **UI 死按钮**：已遍历 PlatformScreen/DeviceScreen/MessageScreen/ApiScreen/DashboardScreen 的全部按钮回调，均指向 `MainActivity` 中真实实现的方法，未发现空回调或调用不存在接口的情况。

---

## 四、重点回答（三段式）

**确认已正确实现**：全部读写接口（设备写操作、SIM 映射、消息已读/删/批量删/保留清理、管理员改密、脚本令牌增/列）的 path·method·请求与响应字段名逐条与后端一致；`DEVICE_AUTH_KINDS` 与后端契约完全对齐；SIM 脱敏号码不再预填（历史 bug 已修）；批量删除确认文案已改为"已加载的全部"且与实际行为一致；`ApiScreen` 地址全部正确（含 `/api/v1/ingest/messages` 与设备指令轮询地址，无旧 `/api/v1/ingest` 笔误）；改密需当前密码、脚本令牌无撤销入口（与后端一致）；连接失败诊断（地址/HTTP/原因）仍在。

**发现的问题（文件:行号 + 严重度）**：
- 中 `RelayAdminClient.kt` 全书无 command 方法 + `ApiScreen.kt:24` + `DeviceScreen.kt`：远程控制 UI 完全未做（后端指令接口已存在，管理端未接）。
- 中 `RelayAdminClient.kt:209-214` + `MainActivity.kt:742-755`：批量删除承诺删"已加载全部"，但后端 bulk-delete 上限 500，已加载 >500 条时 `require` 抛异常导致操作失败。
- 中 后端 `relay.py:662-687`（`_serialize_message` 不输出 `otp_code`）：REST 拉取的历史短信无验证码，仅 WebSocket 实时推送的有，管理端显示不一致。
- 低 `MainActivity.kt:423`：下载地址依赖相对路径，绝对 URL 会重复拼接；低 `relay.py:879`：`https_enabled` 用 public_base_url 前缀推算，可能误导。

**无法确认的疑点**：`release_catalog` 的 `download_url` 相对/绝对格式未确认（影响 #4 是否真出错）；管理端未接指令属预期缺失而非回归。
