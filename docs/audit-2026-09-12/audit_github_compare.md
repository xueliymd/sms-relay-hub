# 自建短信中转平台 vs SmsForwarder（GitHub: pppscn/SmsForwarder）对照审计报告

> 只读分析，未修改任何文件。所有结论均附证据：要么是 SmsForwarder 仓库的具体文件路径/代码片段（raw 抓取于 `https://raw.githubusercontent.com/pppscn/SmsForwarder/master/`），要么是本项目的 `文件:行号`。
> SmsForwarder 当前主包名为 `cn.ppps.forwarder`（v3.5.0 起由 `com.idormy.sms.forwarder` 改名），下文引用以仓库实际代码为准。
> 注：平台（服务端）代码不在本仓库内，凡涉及"服务端是否强制/校验"的结论均明确标注为"依赖服务端，本仓无法核验"。

---

## 0. 方法与证据来源

- SmsForwarder 仓库直接抓取：`AndroidManifest.xml`、`service/ForegroundService.kt`、`utils/HttpServerUtils.kt`、`build.gradle`（保活 Cactus 依赖）、DeepWiki `2.3-services-and-background-processing` 与 `5-remote-control-system`/`5.1-server-configuration`。
- Android 官方文档核实：`developer.android.com/about/versions/15/behavior-changes-15` 确认 `dataSync` 前台服务 6 小时/24 小时预算。
- 本项目：grep `AlarmManager` 全仓库无匹配（`android/` 下 0 处）；逐文件阅读 `collector` 与 `manager-android` 源码。

---

## A. 保活 / 复活机制

### A1. 前台服务类型：`specialUse` 的选择是否正确？

**本项目做法**（正确且合规）：
- `SmsRelayForegroundService.kt:22-34`：API≥34 时 `ServiceCompat.startForeground(..., FOREGROUND_SERVICE_TYPE_SPECIAL_USE)`。
- `AndroidManifest.xml:14` 声明 `FOREGROUND_SERVICE_SPECIAL_USE` 权限；`:40` 声明 `android:foregroundServiceType="specialUse"`；`:46-48` 提供必需的 `<property android:name="android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE" .../>`。
- 注释解释了理由：`dataSync` 在 Android 15 有 6h/24h 运行预算，超时系统强停。

**SmsForwarder 做法**（依据仓库）：
- `AndroidManifest.xml`（WebFetch）中 5 个 `<service>` 仅 `LocationService` 声明了 `foregroundServiceType="location"`，**`ForegroundService` 未声明任何 foregroundServiceType**。
- `service/ForegroundService.kt`（WebFetch）使用旧式两参 `startForeground(FRONT_NOTIFY_ID, notification)`，代码中**无任何前台服务 TYPE 常量**，也**未使用 AlarmManager / Cactus**（Cactus 仅作为 `build.gradle` 依赖存在）。

**官方依据**：`developer.android.com` 明确——以 Android 15(API35) 为目标时，`dataSync`/`mediaProcessing` 前台服务 24 小时内累计运行上限 **6 小时**，超时调用 `onTimeout()`，不停止则崩溃。**`specialUse` 不受此预算限制**。

**结论（A1）**：
1. 本项目选 `specialUse` 的**理由成立**：对一个"常驻等待短信"的监听服务，`dataSync` 的 6h/24h 预算确实会在长时间后台运行后导致被系统强停，而 `specialUse` 无此时限，是更合适的类型。**这不是错误，是正确的工程判断。**
2. 本项目的 `specialUse` 用法**实现合规**：manifest 同时声明了权限 + 类型 + `<property>` 子类型说明，在 API34+ 上不会抛 `MissingForegroundServiceTypeException`——这正是 SmsForwarder 两参写法在 API34+ 上的隐患（除非其 targetSdk < 34 或在别处补了类型）。
3. 副作用提示（低风险）：`specialUse` 在 Google Play 上几乎不被允许上架；但本项目为自部署 sideload，无影响。另：本服务还与 `SYSTEM_ALERT_WINDOW` 双 1x1 悬浮窗并用，Android 15 对"持有 SYSTEM_ALERT_WINDOW 时启动前台服务"有额外限制（`behavior-changes-15` 已列出），需确认该组合在 Android 15 上不被系统拒绝（本仓库代码：`SmsRelayForegroundService.kt:76-81` attachAppOverlayIfPossible）。

### A2. 保活/复活到底靠什么？移除心跳闹钟是否削弱"息屏被杀后按时复活"？

**SmsForwarder 的真实保活栈**（依据仓库）：
- `ForegroundService` + `START_STICKY`（`ForegroundService.kt:214-242`）。
- **Cactus 保活库** `com.gyf.cactus:cactus:1.1.3-beta13`（`build.gradle` 注释原文："Android Keep Alive(安卓保活)，Cactus 集成双进程前台服务，JobScheduler，onePix(一像素)，WorkManager，无声音乐"）。即：**双进程守护 + 1 像素 Activity + JobScheduler + 无声循环音乐(60~120s) + WorkManager**。
- `SCHEDULE_EXACT_ALARM` 权限（`AndroidManifest.xml`），说明其使用了精确闹钟。
- `BootCompletedReceiver` 监听 `BOOT_COMPLETED / LOCKED_BOOT_COMPLETED / USER_PRESENT / PACKAGE_RESTARTED`（多广播兜底自启）。
- WorkManager 仅用于**延迟发送任务**（`SendWorker/SendLogicWorker/UpdateLogsWorker`），并非保活主锚点。

**本项目保活栈**（依据源码）：
- `SmsRelayForegroundService` + `START_STICKY`（`SmsRelayForegroundService.kt:37-52`）。
- **无障碍服务系统重连**为主锚点：`RelayKeepAliveAccessibilityService.kt:8-22`（`onServiceConnected` 续挂前台服务、登记看门狗）；`:38` `onUnbind→return true`——这是无障碍服务区别于普通 Service 的"被杀后系统自动重绑"能力。
- **WorkManager 15 分钟看门狗**为唯一时间驱动锚点：`KeepAliveWatchdog.kt:21`（`PeriodicWorkRequestBuilder<KeepAliveWorker>(15, MINUTES)`）；`KeepAliveWorker.kt:8-20`。
- 开机自启 `BootReceiver.kt`（BOOT_COMPLETED + MY_PACKAGE_REPLACED）；通知栏磁贴 `RelayTileService.kt`；双 1x1 悬浮窗 `KeepAliveOverlay.kt`。
- **全仓库 grep `AlarmManager` 0 匹配**——确认已彻底移除心跳闹钟。

**关键争点结论（A2）——"移除心跳闹钟只留 WorkManager 会削弱息屏复活"这一担忧，部分成立：**
- 对"**活着/复活**"（进程被杀后能否回来）：本项目用**无障碍服务的系统级自动重绑**替代了 SmsForwarder 的 Cactus 闹钟。无障碍重绑是**事件驱动、不受 Doze 批量推迟影响**，作为"生死"锚点其实**强于**一个周期 WorkManager。SmsForwarder 也**不靠**无障碍保活。所以"复活"能力本身并未因移除闹钟而崩溃。
- 对"**按时/定期唤醒**"（例如息屏深 Doze 下仍要把待执行远程指令在有限时延内拉回来）：**移除 `setExactAndAllowWhileIdle` 是真实的削弱**。Android 文档明确：WorkManager 周期任务最小 15 分钟且**遵守 Doze、会被系统批量推迟**，在深 Doze（息屏静止一段时间）下不会在 15 分钟到点精确唤醒；而 `AlarmManager.setExactAndAllowWhileIdle`（配 `SCHEDULE_EXACT_ALARM`）或 `setAlarmClock` 才是**唯一**能穿透 Doze 按时刻唤醒的 API。SmsForwarder 既用 Cactus（其内含 AlarmManager/账号同步唤醒）又声明了 `SCHEDULE_EXACT_ALARM`，保留了该穿透源；本项目移除后，**当设备处于深 Doze、且无新短信触发、且无障碍服务未启用/被关时，唯一的周期唤醒只剩被推迟的 WorkManager，远程指令时延可能远超 15 分钟**。
- **更重要的隐藏前提**：本项目的强保活（无障碍重绑 + 自动重启）依赖 `WRITE_SECURE_SETTINGS`（`AccessibilityAutoToggle.kt:18-87`，需 ADB/Shizuku 授予）。**若该权限未授予**，无障碍需用户手动开启，否则在"无障碍未开"这一回退场景下，本项目的保活锚点只剩前台服务 + WorkManager + 开机广播——**此时保活强度显著弱于 SmsForwarder 开箱即用的 Cactus（无需任何特殊权限即可 1 像素+无声音乐保活）**。

**结论（A2）**：
- "移除心跳闹钟"是**中等严重度**的真实回归，影响的是**深 Doze 下的"按时"唤醒/轮询时延**，而非"复活"本身（复活由无障碍兜底）。
- 建议补回 `AlarmManager.setExactAndAllowWhileIdle`（或 `setAlarmClock`）作为穿透 Doze 的定时唤醒源，与无障碍、WorkManager 并存，而非二选一。

### A3. 引导顺序：与 SmsForwarder "设置向导"相比缺了什么？

**SmsForwarder 向导/通用设置**引导（社区文档+README 归纳）：①开前台服务常驻；②关闭电池优化/设为无限制；③允许自启动；④**多任务界面锁定应用卡片**（防误划杀）；⑤授予全部权限；⑥**开启 Cactus 增强保活 + 后台无声音乐**；⑦**按厂商关闭"验证码安全保护"**（华为/小米/OPPO/vivo 会拦截验证码广播）。

**本项目引导**（`MainActivity.kt:494-514` 后台设置提醒 + 各处按钮）：通知常驻✓、关电池优化✓、后台不受限✓、自启动✓、无障碍保活✓、磁贴✓、ADB 授予 WRITE_SECURE_SETTINGS✓。

**缺口**：
1. **（中）缺"按厂商关闭验证码安全保护"的引导**：我们的提醒未提及。在华为/小米/OPPO/vivo 上，系统"验证码安全保护"会拦截 `SMS_RECEIVED` 广播，导致采集端**漏收验证码类短信**——这是用户最易感知的"为什么收不到"问题，SmsForwarder 明确引导关闭。
2. **（低）"从最近任务移除"按钮与保活最佳实践相悖**：`MainActivity.kt:113-115` 提供"隐藏应用窗口（从最近任务移除）"→ `finishAndRemoveTask()`。SmsForwarder 反过来建议**锁定**应用卡片。对常驻 FGS 应用，`finishAndRemoveTask` 在部分 ROM 上会连带结束进程/任务，与保活目标矛盾（若此为"隐身"刻意设计则另说，但应提示用户代价）。
3. **（低）自启动引导仅为打开应用详情页**：`BackgroundRuntime.kt:91-97` `openAutoStartSettings` 只跳应用详情，未做 SmsForwarder 式的**按厂商 Deep Link**（华为/小米/OPPO/vivo 各自组件）。用户需自行寻找开关，体验较差但功能可达。

---

## B. 远程控制形态

### B1. SmsForwarder 的远程控制形态（依据仓库）

- **设备内置 HTTP Server（AndServer）**：`HttpServerService.kt` 在设备本地起 AndServer，控制方**直连设备 IP:端口**（`HttpServerUtils.kt` 默认端口 5000，v3.5.0 起可自定义）。
- **四种安全档位** `safetyMeasures`：`0` 无 / `1` Sign(HMAC) / `2` RSA / `3` SM4（`HttpServerUtils.kt`）。
  - Sign：`calcSign = HmacSHA256("$timestamp\n" + signSecret)`；`checkSign` 校验时间戳在 `timeTolerance`(默认 600s) 内 + 签名一致。
  - RSA：客户端用 `serverPublicKey` 加密；SM4：16 位密钥对称加密。
- **端点开关**（`HttpServerUtils.kt`）：`enableApiSmsSend`(发短信)、`enableApiSmsQuery`(查短信)、`enableApiCallQuery`(查通话)、`enableApiContactQuery`(查通讯录)、`enableApiContactAdd`(加联系人)、`enableApiBatteryQuery`(查电量)、`enableApiWol`(WOL)、`enableApiLocation`(找手机/定位)、`enableApiClone`(一键克隆配置)。

### B2. 本项目形态：平台下发 + 设备轮询拉取

- 轮询：`DeviceCommandClient.kt:23-49` → `GET /devices/{id}/commands/pending`，`POST .../commands/{id}/result`；HMAC 签名（`SmsRelaySigner.kt`，`timestamp\nnonce\nsha256(body)`）。
- 指令类型仅 3 种：`query_battery` / `query_recent_sms` / `trigger_upload`（`CommandSettingsStore.kt:29-37`），且**端侧授权默认全关**（`CommandSettingsStore.kt:6-11`），未授权只回传说明——安全姿态良好。
- 拉取触发点：短信到达上报后顺带轮询（`SmsRelayUploadRunner.kt:48`），以及 15 分钟 WorkManager 看门狗（`KeepAliveWorker.kt:19-20`）；`DeviceCommandPoller.kt:201-226` 带 30s 去抖与重入锁。

### B3. 评估（可靠性/时延/功耗/安全）

**与直连相比，轮询形态本身的取舍（多为"合理不同"，非错误）：**
- **时延**：直连近实时；轮询在闲时深 Doze 下最坏可达数十分钟（叠加 A2 的 Doze 推迟）。这是轮询固有代价，非实现 bug，但应写入用户预期。
- **功耗/可达性**：轮询无需设备暴露入站端口，**天然绕过 NAT/防火墙**，比直连更省心；代价是周期性网络唤醒略增耗电。架构取舍合理。
- **安全**：
  1. **（中/高）结果机密性缺加密档位**：本项目远程控制**只有 HMAC 签名、无加密层**。`query_recent_sms` 回传的短信正文以明文（仅签名）经 `baseUrl` 传输，而 `SmsRelayConfig.validateBaseUrl`（`SmsRelaySigner.kt:39-47`）**允许 http**。SmsForwarder 至少提供 RSA/SM4 两档加密。若 `baseUrl` 为 http 或 https 被 MITM，最近短信内容可泄露。**建议：baseUrl 强制 https，或增加结果加密档位。**
  2. **（中）重放/ nonce 防护依赖服务端**：客户端虽带 `nonce`，但签名未绑定 URL/方法；SmsForwarder 的 Sign 档也仅做时间戳窗口(600s)校验、无 nonce 缓存。两端均**仅靠时间窗防重放**；本项目的 nonce 若服务端不强制唯一则形同装饰。**需服务端对 (deviceId, nonce) 去重**，本仓无法核验。
  3. **（中）指令执行缺乏端侧幂等/原子领取**：`pendingCommands` 取出后逐个执行再 `postResult`，进程在"取-执行-回传"之间被杀会**重复执行同一指令**（`DeviceCommandPoller.kt:201-226` 的重入锁仅限同进程）。当前 3 类指令皆只读/触发上报，危害有限；但若未来加入"远程发短信"等写操作则会出现重复发送。建议服务端对 command 做原子 `claim` 状态机。
  4. **（低）撤销设备后仍可取指令**：若设备被撤销但 `hmacSecret/deviceId` 未失效，服务端若不校验 `active` 状态，已撤销设备仍能轮询取指令。属服务端职责，本仓无法核验，列出供关注。
  5. **（低）HttpURLConnection 默认跟随重定向**：`DeviceCommandClient.kt:51-78` 用 `HttpURLConnection` 且未禁用重定向，带签名的请求可能被 302 引向攻击者主机导致签名/数据泄露（SSRF/开放重定向）。建议关闭重定向或对重定向后目标重新签名。

**缺失的关键能力（对照 SmsForwarder 端点清单）：**
| SmsForwarder 端点 | 本项目 | 判断 |
|---|---|---|
| 发短信 `enableApiSmsSend` | **缺失** | **漏了（高价值）**：SIM 卡手机最实用的远程能力是"代发短信"，本项目未实现 |
| 查短信 `enableApiSmsQuery` | `query_recent_sms`（仅最近 5 条、sender 脱敏） | 有意精简（脱敏合理），但"查全部/按条件"缺失 |
| 查通话 `enableApiCallQuery` | 缺失 | 有意不做（采集端仅收短信） |
| 查/加通讯录 | 缺失 | 有意不做 |
| 查电量 `enableApiBatteryQuery` | `query_battery` ✓ | 已实现 |
| WOL `enableApiWol` | 缺失 | 有意不做/低优先级 |
| 找手机/定位 `enableApiLocation` | 缺失 | 有意不做 |
| 一键克隆配置 `enableApiClone` | 缺失 | 有意不做（自部署架构不同） |
| 触发上报 `trigger_upload` | ✓ | 已实现 |

> 结论（B）：轮询形态本身**合理**；真正的问题是**①结果无加密档位（明文/可 http）②发短信能力缺失③执行幂等/防重放依赖服务端未闭环**。其中 ① 是安全真问题，② 是最大能力缺口。

---

## C. 短信转发 / 采集端核心链路

SmsForwarder 转发：多渠道（钉钉/企微/飞书/Bark/Webhook/Telegram/SMTP/Server酱/PushPlus 等 13+）、规则按号码/内容/正则/SIM 匹配、模板变量 `{{FROM}}{{SMS}}{{CARD_SLOT}}{{RECEIVE_TIME}}` 等、失败重试 5 次（可配间隔，成功即止）、转发日志。

**本项目采集端（依据源码）：**
1. **本地加密存储（优于 SmsForwarder）**：`SmsRelayLocalCipher.kt` 用 `AndroidKeyStore` + `AES/GCM/NoPadding` 对队列中的 `sender/body/phoneNumber` 做 `seal`（`:15-19` 入库即加密），读取时 `open` 解密。SmsForwarder 配置/日志多为明文存储。这是本项目**正确且更安全的设计**。
2. **幂等（正确）**：`SmsRelayPayload.kt:52-69` `clientMessageId = "android-app-v1:" + sha256(sender\nmessageDateMillis\nbody)`；服务端对重复 `client_message_id` 返回 Conflict→`SmsRelayQueueRepository.kt:74-80` `markConflict`。键稳定，重复广播可去重。**唯需注意**：若同号同正文同毫秒出现两次（极少见），会被误判为同一条——可接受。
3. **重试策略（与 SmsForwarder 不同，非错误但需注意）**：`SmsRelayUploadRunner.kt` 失败→`markRetry`→`UploadScheduler.enqueue`（WorkManager，`UploadScheduler.kt:22-29`，指数退避 30s）。SmsForwarder 是**显式 5 次重试封顶**；本项目依赖 WorkManager 的**近乎无限重试**（最终送达语义）。对短信转发"宁可晚到不要丢"是合理取向，但**缺少明确的失败上限/死信处理**：若服务端持续返回"可重试"(5xx/网络)，队列会长期 pending 并反复唤醒 WorkManager；而 `AuthBlocked`(401/403) 已单独处理并暂停（`:84-89`、`:132-135`），设计周全。建议对 `PermanentFailure` 之外的长期 pending 设观察指标。
4. **模板变量语义（窄于 SmsForwarder，属能力缺口非 bug）**：采集端上报字段为 `sender/body/sim_slot` + metadata(`subscription_id/carrier_name/phone_number/collector_instance_id/collector_version/collector_mode`)。**缺少** SmsForwarder 的 `{{CONTACT_NAME}}`(通讯录反查)、`{{PHONE_AREA}}`(归属地)、`{{BATTERY_PCT}}`(采集时电量)、`{{DEVICE_NAME}}` 等。平台侧模板可用变量相应受限——若平台规则引擎需要这些变量，采集端未提供即无法实现。属"漏了/有意不做"，需在需求层面确认。
5. **目标加密存储/传输**：本地队列加密✓（见上）；上行到平台走 HMAC 签名，但同样允许 `http`（`SmsRelayConfig.validateBaseUrl` 允许 http），短信正文上行若走 http 亦明文。与 B1 同属"应强制 https"问题。

**结论（C）**：采集端核心链路**实现正确**（本地加密、幂等、鉴权阻断、退避重试均到位）。主要不足是**模板变量集偏窄**与**上行允许 http**——前者是能力缺口，后者是安全加固项。

---

## D. 管理端（Android）

SmsForwarder 没有独立"管理端"——其 App 自身即设备侧配置中心（发送通道、转发规则、SIM 备注、免打扰、日志等）。本项目管理端 `manager-android` 实为**平台管理员控制台**（`RelayAdminClient.kt` + `MainActivity.kt`）：设备增删/凭据轮换/撤销恢复、SIM 号码映射、短信实时 WebSocket、消息已读/删除/批量删除、保留期清理、脚本 API 令牌、管理员账号、版本历史与下载。**远程管理能力已远超 SmsForwarder 单机 App。**

**用户会明显感知的缺失（管理端相对 SmsForwarder 设备侧配置）：**
1. **（中）管理端无法编辑"转发规则/发送通道"**：`RelayAdminClient.kt` 无任何 rule/sender 端点。SmsForwarder 最强卖点就是**在 App 内按号码/内容/正则/SIM/免打扰配置转发规则**；本管理端只能看消息与管设备，**无法配置"把 10086 的短信转到某通道"**。若平台侧有规则引擎但管理端未暴露 UI，这是明显的能力缺口（需确认是否为有意分期）。
2. **（低）管理端不能下发/触发远程指令**：`RelayAdminClient.kt` 无"issue command"接口，即管理端**不能查某设备电量/最近短信/触发上报**（这些仅平台 Web 端可用）。与 B 节"发短信能力缺失"同源。
3. **（低/有意）采集端本地捕获设置不可在管理端配置**：SIM 备注由采集端自动识别 + 管理端手动补映射；"转发应用通知/通话"等本采集端就未做（范围不同）。属架构差异，非缺陷。

**结论（D）**：管理端作为平台控制台已很完整；唯一明显"用户会想要却没有"的是**转发规则/发送通道的可视化配置入口**（大概率为平台侧未开放，而非实现错误）。

---

## E. 结论：我们做错 / 做漏了什么（排序清单）

### 高（真问题，建议尽快处理）
- **H1. 远程控制结果无加密档位 + baseUrl 允许 http**（`SmsRelaySigner.kt:39-47`、`DeviceCommandClient.kt:41-49`）：`query_recent_sms` 回传明文短信可经 http 泄露。SmsForwarder 至少提供 RSA/SM4。→ **强制 https；建议增加结果加密档位。**
- **H2. 远程"发短信"能力缺失**（对照 `HttpServerUtils.kt` `enableApiSmsSend`）：SIM 卡手机最有价值的远程能力未实现。→ 评估补齐 `send_sms` 指令 + 端侧授权 + 执行幂等。

### 中（真实回归/缺口，应处理）
- **M1. 移除心跳 AlarmManager 削弱深 Doze 下"按时"唤醒**（`KeepAliveWatchdog.kt:21`、grep 无 AlarmManager）：复活由无障碍兜底，但定时轮询时延在深 Doze 下可能远超 15 分钟。→ 补回 `setExactAndAllowWhileIdle` 作为穿透 Doze 的保活闹钟。
- **M2. 强保活依赖 WRITE_SECURE_SETTINGS，缺失时弱于 SmsForwarder 开箱保活**（`AccessibilityAutoToggle.kt:18-87`）：未授权时退回"无无障碍"场景，保活锚点只剩前台服务+WorkManager，弱于 Cactus（无需权限）。→ 提供不依赖无障碍的备选保活（如无声音乐/1 像素），或明确告知用户必须授予该权限。
- **M3. 缺"按厂商关闭验证码安全保护"引导**（`MainActivity.kt:494-514`）：导致华为/小米/OPPO/vivo 漏收验证码。→ 向导中加入分厂商指引。
- **M4. 指令执行无端侧幂等/原子领取**（`DeviceCommandPoller.kt:201-226`）：进程中途死亡会重复执行。当前只读指令危害小，但上线"发短信"等写指令前必须补。→ 服务端 command 原子 claim + 客户端去重。
- **M5. 管理端缺转发规则/发送通道配置 UI**（`RelayAdminClient.kt` 无 rule/sender 端点）：用户无法在管理端配置转发逻辑。→ 确认平台是否提供，补齐管理端入口。

### 低（合理不同 / 锦上添花）
- **L1. 模板变量集窄于 SmsForwarder**（`SmsRelayPayload.kt:78-100`）：缺通讯录反查/归属地/采集时电量等。属能力缺口，按需补。
- **L2. `HttpURLConnection` 默认跟随重定向**（`DeviceCommandClient.kt:51-78`）：签名请求可能被 302 引向外部。→ 关闭重定向或重签。
- **L3. "从最近任务移除"按钮与保活相悖**（`MainActivity.kt:113-115`）：部分 ROM 会连带结束进程。→ 提示代价或改为"锁定"建议。
- **L4. 自启动引导仅打开应用详情页**（`BackgroundRuntime.kt:91-97`）：未做分厂商 Deep Link，体验弱于 SmsForwarder。
- **L5. 重放防护仅靠时间窗**（同 SmsForwarder Sign 档）：依赖服务端 nonce 去重，本仓无法核验。

### 明确"与开源不同但合理 / 做对了"的点（不要误判为问题）
- ✅ 选 `specialUse` 前台服务类型：理由（dataSync 6h/24h 预算）成立、实现合规、优于 SmsForwarder 的两参写法。
- ✅ 用无障碍服务系统重绑作"复活"主锚点：事件驱动、抗 Doze，是 SmsForwarder 未采用的有效替代方案。
- ✅ 本地队列 AES/GCM(AndroidKeyStore) 加密：优于 SmsForwarder 明文存储。
- ✅ `client_message_id` 幂等去重 + AuthBlocked 暂停 + 指数退避：核心链路正确。
- ✅ 远程指令端侧授权默认全关：安全姿态优于 SmsForwarder 默认开启各 API。
- ✅ 轮询替代设备直连 HTTP Server：绕过 NAT/防火墙，架构取舍合理（代价是时延，已说明）。
- ✅ HMAC 签名绑定 `timestamp\nnonce\nsha256(body)`，比 SmsForwarder Sign 档（仅 `timestamp+secret`）更强（多了 body 完整性）。

---

### 一句话总评
采集端与平台侧的**核心链路实现是正确的**（本地加密、幂等、鉴权、退避都对）；与 SmsForwarder 相比，**真正做错/做漏**集中在三处：**①远程控制结果明文可走 http（安全真问题）、②远程发短信能力缺失（最大能力缺口）、③移除心跳闹钟后深 Doze 下"按时"唤醒被削弱**；其余多为"合理不同"或低优先级体验项。
