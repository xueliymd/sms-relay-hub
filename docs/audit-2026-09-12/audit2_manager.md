# 管理端（io.smsrelay.manager 1.0.8）全量复核审计报告

> 只读审查，未修改任何文件。证据均附 `文件:行号`。
> 代码根：`manager-android/app/src/main/java/io/smsrelay/manager/`，后端 `backend/app/api/relay.py`。

## 一、结论速览

### ✅ 真做对了（功能真实接线且正确）
- 设备写操作全套：创建/轮换/撤销/恢复/永久删除，均带二次确认（`MainActivity.kt:557-564` 的 `confirm()`），凭据"仅显示一次"弹窗（`MainActivity.kt:606-613` 的 `showCredentialDialog`），后端对应端点齐全。
- SIM 号码映射：编辑框**不预填**脱敏号码（`MainActivity.kt:689-690` 注释+`hint`），避免把掩码当真实值提交；改/删后端 `source=manual` 与 `SIM_PROFILE_SOURCES` 一致。
- 消息：已读/未读、单条删除、保留清理（retention）均真实调用后端。
- 管理员账号修改后强制重登（`MainActivity.kt:828` 调 `logout()`）。
- 接口页 9 个地址全部正确、无笔误（`ApiScreen.kt:19-33`，逐条对照后端见下表）。
- 实时 WebSocket：鉴权/订阅/心跳/退避重连完整（`AdminMessageSocket.kt:47,101-111,94-99`）；时间线合并+补拉（`MessageTimeline.kt:16-21,27` + `MainActivity.kt:495-500`）。
- 版本更新检查（`MainActivity.kt:338-350`）、连接失败诊断显示请求地址/HTTP 状态码/系统原因（`MainActivity.kt:872-879`）。
- 脚本令牌：新建✅、列表✅。

### ⚠️ 确实错了 / 有缺陷
- **(a) 批删 500 上限**：`RelayAdminClient.kt:210` `require(messageIds.size <= 500)`，而时间线容量 5000（`MainActivity.kt:51`），加载>500 条后点批量删除会**优雅报错但操作不执行**。后端同样限 500（`relay.py:1822`），故非崩溃，但属体验缺陷/文档未说明。
- **(b) REST 拉取短信不含 otp_code**：历史/补拉短信不显示"验证码"按钮，仅 WS 实时推送带 `otp_code`。`_serialize_message`（`relay.py:662-687`）不输出该字段，只有 `_publish_admin_message`（`relay.py:694`）在 WS 路径补。

### 📄 文案与行为不符
- `PENDING_FEATURES_REPORT_2026-09-12.md:19` 把管理端 1.0.7"批删"列入 ✅ 全套，但批删有 500 上限缺陷未注明。
- 脚本令牌"撤销"：`PENDING` 文档 ✅ 仅指新建+列表，撤销入口 UI 与后端均无（见下），文档未宣称撤销故不算谎称，属**遗漏缺口**。

### ❌ 未接线 / 缺失
- **(c) 远程控制 UI 缺失**：管理端无"下发指令 / 查看指令结果"界面，仅 `ApiScreen.kt:24` 把设备轮询 URL 当静态文本展示。后端 commands 端点齐全（`relay.py:996-1096`）。`FULL_AUDIT_REPORT_2_2026-09-12.md:74` 已自承"管理端 App 无远程控制下发入口"——文档与行为一致地承认了缺失。

## 二、逐项核对表

| 功能项 | 声称 | 代码位置 文件:行 | 后端对应端点 | 实际行为 | 判定 | 证据 |
|---|---|---|---|---|---|---|
| 创建设备 | 创建+一次性凭据 | `MainActivity.kt:615-651` / `RelayAdminClient.kt:147-160` | `POST /admin/devices` `relay.py:791` | 弹窗填名/类型→返回 device+credential→仅显示一次 | ✅ | `MainActivity.kt:647` |
| 轮换凭据 | 旧失效+新凭据仅显示一次 | `MainActivity.kt:653-660` / `RelayAdminClient.kt:163-165` | `POST .../rotate-credential` `relay.py:889` | 二次确认→返回新 secret→弹窗 | ✅ | `relay.py:898` |
| 撤销设备 | 软删可恢复 | `MainActivity.kt:662-668` / `RelayAdminClient.kt:173-174` | `DELETE /admin/devices/{id}` `relay.py:984` | 二次确认执行 | ✅ | `relay.py:985` |
| 恢复设备 | 生成新凭据 | `MainActivity.kt:670-677` / `RelayAdminClient.kt:168-170` | `POST .../restore` `relay.py:901` | 返回新 secret→弹窗 | ✅ | `relay.py:910` |
| 永久删除 | 不可恢复 | `MainActivity.kt:679-685` / `RelayAdminClient.kt:177-178` | `DELETE .../purge` `relay.py:913` | 二次确认执行 | ✅ | `relay.py:913` |
| SIM 改号码 | 不预填掩码 | `MainActivity.kt:687-713` | `PUT .../sim-profiles/{slot}` `relay.py:951` | 编辑框仅 hint，不预填脱敏号 | ✅ | `MainActivity.kt:690` |
| SIM 删映射 | 删除映射 | `MainActivity.kt:715-721` / `RelayAdminClient.kt:190-191` | `DELETE .../sim-profiles/{slot}` `relay.py:970` | 二次确认执行 | ✅ | `relay.py:970` |
| 已读/未读 | 切换 | `MainActivity.kt:723-730` / `RelayAdminClient.kt:194-202` | `POST .../read`·`/unread` `relay.py:1777,1789` | 切换并本地 merge | ✅ | `relay.py:1784` |
| 单条删除 | 删除 | `MainActivity.kt:732-740` / `RelayAdminClient.kt:205-206` | `DELETE /admin/messages/{id}` `relay.py:1801` | 二次确认执行 | ✅ | `relay.py:1801` |
| 批量删除 | 删当前列表全部 | `MainActivity.kt:742-755` / `RelayAdminClient.kt:209-214` | `POST /admin/messages/bulk-delete` `relay.py:1813` | **加载>500 条时 `require` 抛异常→操作失败** | ⚠️ | `RelayAdminClient.kt:210` |
| 保留清理 | retention | `MainActivity.kt:757-763` / `RelayAdminClient.kt:217-219` | `POST /admin/retention/run` `relay.py:1837` | 返回脱敏条数 | ✅ | `relay.py:1840` |
| 管理员账号修改 | 改后重登 | `MainActivity.kt:791-833` / `RelayAdminClient.kt:222-232` | `PUT /admin/account` `relay.py:761` | 成功后 `logout()` 强制重登 | ✅ | `MainActivity.kt:828` |
| 脚本令牌-新建 | 创建 | `MainActivity.kt:782-789` / `RelayAdminClient.kt:235-237` | `POST /admin/api-clients` `relay.py:1843` | 仅显示一次弹窗 | ✅ | `relay.py:1846` |
| 脚本令牌-列表 | 列出 | `MainActivity.kt:765-780` / `RelayAdminClient.kt:240-249` | `GET /admin/api-clients` `relay.py:1849` | 显示创建时间/启用状态/可复制明文 | ✅ | `ApiScreen.kt:40-54` |
| 脚本令牌-撤销 | (文档未宣称) | **无入口** `ApiScreen.kt:35-57` | **无端点**（仅 create+list） | UI 无撤销按钮；后端无 delete 端点 | ❌缺失 | grep 全仓无 `delete_api_client`/`revoke_api` |
| 接口页地址 | 9 项正确 | `ApiScreen.kt:19-33` | 见下"地址核对" | 9 项全部与后端路由一致 | ✅ | `BaseUrl.kt:9-19` |
| 实时 WebSocket | 推送 | `AdminMessageSocket.kt` + `MainActivity.kt:479-527` | `WS /ws/admin/messages` `relay.py:357` | auth→subscribe→心跳60s→退避重连 | ✅ | `AdminMessageSocket.kt:101-111` |
| 时间显示 | 设备时间优先 | `MessageScreen.kt:221-230` | — | 仅显示单时间 `messageDate ?: receivedAt`（设计取舍） | ✅ | `MessageScreen.kt:223` |
| 时间线合并/补拉 | 合并去重+补拉 | `MessageTimeline.kt:16-27` + `MainActivity.kt:495-500` | `GET /admin/messages`(cursor) `relay.py:1726` | merge 按 id；断线 recover 补拉 | ✅ | `MessageTimeline.kt:17` |
| 版本更新检查 | 检查+刷新 | `MainActivity.kt:338-350,386-418` | `GET /releases/{platform}/history` `updates.py:51` | 显示有无新版本+下载 | ✅ | `ReleasePolicy` 调用 |
| 连接失败诊断 | 显示地址/码/原因 | `MainActivity.kt:872-879` | — | Unauthorized/Network/Http 全带 url+原因 | ✅ | `MainActivity.kt:873-875` |
| 远程控制 UI | 下发/结果 | **无** | `POST /admin/devices/{id}/commands` `relay.py:996` 等 | 管理端无任何指令发送/结果查看界面 | ❌缺失 | grep 仅 `ApiScreen.kt:24` 静态文本 |

**接口页地址核对（无笔误）**：`/api/v1/health`(`relay.py:31`)、`/ingest/messages`(`1621`)、`/ingest/shortcut`(`1493`)、`/ingest/mqtt`(`1569`)、`/devices/{id}/commands/pending`(`1037`)、`/admin/messages`(`1726`)、`/ws/admin/messages`(`357`)、`/otp/claim`(`1661`)、`/ws/otp`(`265`)。全部正确。

## 三、三条已知疑点的定论

### (a) 批删 >500 条因 `require(size<=500)` 抛异常导致失败 —— 属实（带限定）
- **证据**：`RelayAdminClient.kt:210` `require(messageIds.isNotEmpty() && messageIds.size <= 500)`；时间线容量 `MainActivity.kt:51` `MessageTimeline(capacity = 5000)`；`loadMoreMessages` 每页 500、offset 自 500 递增（`MainActivity.kt:444-477`），加载两页后即达 1000 条，`bulkDeleteMessages` 取 `timeline.items()`（`MainActivity.kt:743`）→ 触发 require。
- **影响面**：加载>500 条时，点"批量删除当前列表"会进入 `runAdminOp` 的 `runCatching`（`MainActivity.kt:572`）捕获 `IllegalArgumentException`→ 显示"批量删除需要 1~500 条消息"（`MainActivity.kt:879`），**不执行删除、不崩溃**。后端同样 `len>500` 拒绝（`relay.py:1822`）。属体验缺陷而非崩溃。
- **修复建议**：在 `bulkDeleteMessages`（`MainActivity.kt:742-755`）按 500 切片循环调用，或改为按 `device_id`/时间范围后端批量清理；同时放宽或对齐前后端上限说明。

### (b) REST 拉取短信不含 otp_code，仅 WS 带 —— 属实
- **证据**：REST 列表走 `_serialize_message`（`relay.py:662-687`），返回字段含 `otp_status` 但**无 `otp_code`**；`otp_code` 仅在 `_publish_admin_message`（`relay.py:690-696`）第 694 行 `event["otp_code"] = ...` 为 WS 实时推送补上。`list_messages` 用 `_serialize_message`（`relay.py:1774`）。管理端 `RelayJson.message` 读 `otp_code`（`RelayAdminClient.kt:339`）、`MessageScreen` 仅在 `otpCode` 非空时显示"验证码"按钮（`MessageScreen.kt:232-235`）。
- **影响面**：实时 WS 到达的短信显示验证码按钮；但通过 REST 历史拉取（`loadMoreMessages` `MainActivity.kt:454`）与 WS 断线补拉（`recover` `MainActivity.kt:496`，同样走 REST `client.messages`）的短信 `otp_code` 恒为 null → **不显示验证码按钮**，与"实时有、历史无"表现一致。
- **修复建议**：在 `_serialize_message`（`relay.py:662`）增加 `otp_code` 输出（从 `store.get_otp_delivery(message.id)` 取，注意过期/已领取状态），使 REST 与 WS 一致；或在 `MessageScreen` 用 `otp_status` 区分。

### (c) 管理端是否有设备指令（commands）调用与界面 —— 属实（缺失）
- **证据**：全仓 grep `command|指令|enqueue|pending` 在 `manager-android` 仅命中 `ApiScreen.kt:24` 的静态文本 `"设备指令轮询" to baseUrl.http("/api/v1/devices/{device_id}/commands/pending")`；`RelayAdminClient.kt` 无任何 `createDeviceCommand`/`listDeviceCommands` 方法；`MainActivity.kt` 无下发/结果回调接线。后端 commands 端点齐全：`POST /admin/devices/{id}/commands`(`relay.py:996`)、`GET .../commands`(`1024`)、`GET /devices/{id}/commands/pending`(`1037`)、`POST .../commands/{id}/result`(`1061`)。
- **影响面**：管理端 App **无法下发远程指令、无法查看指令结果**（仅网页管理台可）。`FULL_AUDIT_REPORT_2_2026-09-12.md:74` 已明确"管理端 App 无'远程控制'下发入口（只有网页有）"——文档自承，非隐瞒。
- **修复建议**：在 `RelayAdminClient` 加 `createDeviceCommand`/`listDeviceCommands`（对齐 `relay.py:996,1024`），新增设备管理页"下发指令"+"结果"区；预计半天（`PENDING_FEATURES_REPORT` 第 4 项已估）。

## 四、与开源项目对比结论（pppscn/SmsForwarder，GitHub README）

- **远程控制形态**：SmsForwarder 为**设备内置 HTTP Server**（依赖 `AndServer`，设备即服务端，控制器直连设备，`README`"主动控制服务端与客户端"）；relay-hub 为**平台下发 + 设备轮询领取执行**（`relay.py:996-1096` + 采集端 `DeviceCommandClient.kt`，见 `FULL_AUDIT_REPORT_2.md:50`）。**差异合理**：relay-hub 无需设备暴露入站端口、规避 NAT/防火墙，且服务端权威、便于审计；代价是管理端当前无下发 UI（见 (c)）。
- **安全档位**：SmsForwarder 远程控制为**单一远程控制密码**保护本地 HTTP Server（README 未提加密/HMAC/RSA/SM4）；relay-hub 用**每设备 HMAC 密钥**（root/air724/ve，`RelayAdminClient.kt:304-311` `DEVICE_AUTH_KINDS`）与 iOS `bearer`，指令结果密文落库（`relay.py:1093`）。**relay-hub 鉴权更强**。注：本仓后端未检索到 RSA/SM4（任务所提"RSA/SM4"未在本项目落地），仅 HMAC/bearer。
- **能力覆盖**：SmsForwarder 支持全 5 类——**发短信、查短信、查通话、查话簿、查电量**（`README`"远程发短信、查短信、查通话、查话簿、查电量"）；relay-hub **v1 仅 查电量/查最近短信/触发上报**（`FULL_AUDIT_REPORT_2.md:54`、 `PENDING_FEATURES_REPORT:48`）。**发短信/查通话/查话簿（v2）是真缺口**，非"合理不同"——SmsForwarder 已具备，本项目 v2 未实现。
- 小结：架构与安全档位 relay-hub 属**合理不同且更优**；能力覆盖（v2 三类敏感指令）与**管理端无指令 UI** 是**真缺口**。

## 五、无法静态确认、必须真机/联调验证的点

1. 设备写操作（`createDevice` 等）实际落库与凭据下发到采集端能否跑通（需真机/网页联调）。
2. SIM 改/删在双卡实体机的槽位/`subscriptionId`/`phone_number_masked` 正确性（`DeviceScreen.kt:56-64` 仅展示）。
3. WebSocket 在弱网下的重连/心跳/补拉时序（`AdminMessageSocket.kt`），需真机长连验证。
4. (b) 修复后 REST `otp_code` 显示需联调 OTP 领取链路（`relay.py:1661` claim 端点）。
5. 远程控制端到端（`PENDING_FEATURES_REPORT:75`）：网页下发→采集端授权→一个上报周期内出结果；管理端 UI 待 (c) 修复后补测。
6. 版本更新检查在真机上的 `ReleasePolicy` 判定与 APK 下载跳转（`MainActivity.kt:420-425`）。
7. 厂商保活/OEM 限制下 WebSocket 长连到达率（非代码静态可定）。
