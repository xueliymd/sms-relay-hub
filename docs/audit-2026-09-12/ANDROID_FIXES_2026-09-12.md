# 采集端 / 管理端 Android 修复清单（2026-09-12）

> 本轮只改**两个 Android 应用**（按你的要求：平台侧留到下一轮）。
> 所有改动均已在本机真实编译通过（`clean` + `testDebugUnitTest` + `lintDebug` + `assembleRelease`）。
> 工具链：JDK17 `C:\Users\12071\.codex\tools\jdk17\jdk17.0.19_10`、Android SDK `C:\Users\12071\.codex\tools\android-sdk`、Gradle 8.9 `C:\Users\12071\.codex\tools\gradle-8.9`（**上次我说"本机没有 JDK"是错的**，工具链一直在 `.codex\tools` 下）。

---

## 一、你反馈的两个问题 —— 修复与定论

### 1.1 电池优化按钮（你说的"黑屏闪一下就结束"）

**你的记忆没错，是选型错了。** Android 有两种跳法，含义完全不同：

| 方案 | 意图 | 表现 |
|---|---|---|
| **跳页面**（你要的） | `ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS`（无 data） | 打开系统"电池优化"列表页，自己手动设 |
| 弹对话框（原实现的首选） | `ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` + `data=package:` | 直接弹"是否允许忽略电池优化？" |

原实现把**弹框当首选、跳页只当失败回退**，这就是"按钮写着'关闭'、整套逻辑按弹框写"的由来。

**黑屏的真因**：原回退分支与共用的 `startFirstResolvable` 都把 `startActivity` 放在 `runCatching` **之外**，目标活动在本 ROM 不可启动时抛 `ActivityNotFoundException`/`SecurityException` 直接冒泡 → 前台进程被终止 → "闪一下就退出"。

**已修**（`BackgroundRuntime.kt`）：
- `openBatteryOptimizationSettings` 改为**页面优先**三级兜底：① 电池优化列表页 → ② 本应用忽略请求页 → ③ 应用详情页。
- `startFirstResolvable` 改为**逐个 `startActivity` + 异常包裹 + 失败继续试下一个**；并**去掉 `resolveActivity` 预判**（Android 11+ 包可见性会让它对系统设置页返回 null，反而把可用候选挡掉）。
- `MainActivity.kt`：按钮文案 `关闭电池优化` → **`打开电池优化设置（手动设为不限制）`**；状态行 → `仍受系统优化限制（点下方按钮去设置）`；引导第 2 条 → `打开电池优化设置，把本应用设为"不优化/不受限制"`。

**与 GKD 对齐**：GKD 的 `permission/PermissionState.kt` 把"忽略电池优化"建模为**权限状态**（先 `isIgnoringBatteryOptimizations` 判断，再把用户引到系统页），不是甩请求框。我们现在的行为与之一致。

### 1.2 磁贴（长按跳应用详情 / 点击关不掉）

已按你的要求**对齐 GKD 修完**，并抓了 GKD 的 `AndroidManifest.xml` 原文核对（不是凭印象）：

| 你的描述 | 根因 | 修法 |
|---|---|---|
| 点击不能关闭 | `onClick` 只有 `revive()`，无停止路径 | 重写为**开/关切换** |
| 显示"运行中"其实已死 | `syncTile` 拿"是否已配置"当"是否在运行" | 用 `RelaySwitch.isEnabled && SmsRelayRuntime.serviceActive` |
| 长按跳应用详情 | manifest 未声明 `QS_TILE_PREFERENCES` | `MainActivity` 加该 intent-filter |
| 关掉又被自动拉起 | 无"用户已暂停"的持久状态 | 新增持久开关 `RelaySwitch`，所有自动拉起链路让路 |

GKD 原文核对结果（`gkd-app/src/main/AndroidManifest.xml`）：
```xml
<activity android:name=".entry.OpenTileActivity" android:exported="true">
  <intent-filter>
    <action android:name="android.service.quicksettings.action.QS_TILE_PREFERENCES" />
  </intent-filter>
</activity>
...
<service android:name=".service.GkdTileService" ...>
  <meta-data android:name="android.service.quicksettings.TOGGLEABLE_TILE" android:value="true" />
```
→ **GKD 每个开关型磁贴都声明 `TOGGLEABLE_TILE=true`，且全清单没有用 `ACTIVE_TILE`。** 我们照此补齐 `TOGGLEABLE_TILE`；`ACTIVE_TILE` 依然不加（它会改变系统绑定语义，必须配 `requestListeningState`，贸然加只会让状态更不刷新）。

**关闭语义已做实**：新增 `RelaySwitch` 持久开关，关闭后**进程启动 / 开机与更新 / 无障碍重连 / 周期看门狗 / 短信到达** 五条链路全部让路；主界面也加了同名"暂停/恢复转发"按钮（磁贴与主界面共用同一状态）。

### 1.3 顺手发现并修掉的真实缺口：暂停后短信仍会外发

`SmsReceivedReceiver` 原来完全不看这个开关——`stopService` 只是停了服务，**收到的短信照样入队并立即上报**，"关闭"名不副实。

现在：`SmsReceivedReceiver` 与 `SmsRelayUploadRunner.runImmediate/runOnce` 都加了闸门 —— **暂停时短信仍写入本地队列（不丢数据），但不发起任何上报；恢复后由队列自动补传。**

---

## 二、采集端（`io.smsrelay.collector`）逐条改动

| # | 文件 | 改动 | 目的 |
|---|---|---|---|
| 1 | `RelaySwitch.kt` **(新增)** | 持久"转发开关"（SharedPreferences，默认开） | 磁贴/主界面可开可关，且"关"能持久 |
| 2 | `RelayTileService.kt` **(重写)** | 点击=开/关；`syncTile` 用真实运行态；标签区分 未配置/运行中/已暂停 | 修磁贴三处缺陷 |
| 3 | `AndroidManifest.xml` | ① `MainActivity` 加 `QS_TILE_PREFERENCES` 过滤器；② 磁贴加 `TOGGLEABLE_TILE=true` | 长按打开应用；声明为开关型磁贴 |
| 4 | `BackgroundRuntime.kt` | 新增 `stop()`；电池优化改"页面优先"三级兜底；`startFirstResolvable` 逐项试+异常包裹 | 修黑屏与选型 |
| 5 | `KeepAliveWatchdog.kt` | 新增 `cancel()`；`enqueuePeriodicWorker` 在已暂停时直接返回 | "关"能生效（否则下次登记会撤销它） |
| 6 | `SmsRelayRuntime.kt` | 新增 `@Volatile serviceActive` | 磁贴/状态栏能判断服务是否真在跑 |
| 7 | `SmsRelayForegroundService.kt` | onCreate/onDestroy 维护 `serviceActive`；`onDestroy` 按窗口类型释放悬浮窗 | 运行态真实；不再互相顶掉 |
| 8 | `KeepAliveOverlay.kt` **(重写)** | 由单个布尔改为 `Map<windowType, View>` 分别记账 | 修"双保险实际只有一层" |
| 9 | `RelayKeepAliveAccessibilityService.kt` | 重申保活前判 `RelaySwitch`；按无障碍窗口类型释放 | 暂停时不被拉起 |
| 10 | `RelayApplication.kt` | 进程启动续登记看门狗前判 `RelaySwitch` | 暂停不被悄悄撤销 |
| 11 | `BootReceiver.kt` | 开机/更新恢复前判 `RelaySwitch` | 同上 |
| 12 | `KeepAliveWorker.kt` | ① 暂停时直接返回；② `AccessibilityAutoToggle.restart` 改到 `withContext(Dispatchers.IO)` | 修"在 suspend 里 `Thread.sleep` 阻塞 WorkManager 线程" |
| 13 | `MainActivity.kt` | 新增"暂停/恢复转发"按钮；电池优化文案与状态行；`开启自启动（厂商设置）` → **`打开应用详情（自行找到自启动/省电设置）`**；保存/扫码时置开关为开 | 文案与行为一致 + 可发现可逆的暂停入口 |
| 14 | `SmsReceivedReceiver.kt` | 新增暂停闸门（仍入队、不上报） | "关闭"语义做实 |
| 15 | `SmsRelayUploadRunner.kt` | `runImmediate`/`runOnce` 开头判 `RelaySwitch` | 覆盖 WorkManager/远程触发等所有上报入口 |
| 16 | `app/build.gradle.kts` | `versionCode 21→22`、`versionName 1.0.20→1.0.21` | 新版本可被更新机制识别 |
| 17 | `android/README.md` | 删掉"应用不再提供会跳转厂商设置页的按钮"（与代码矛盾的不实描述），改为如实说明三个跳转按钮的行为 + 磁贴开关语义 | 文档不再误导 |

---

## 三、管理端（`io.smsrelay.manager`）逐条改动

| # | 文件 | 改动 | 目的 |
|---|---|---|---|
| 1 | `net/RelayAdminClient.kt` | 新增 `deviceCommands(token, deviceId, limit)`、`createDeviceCommand(token, deviceId, type)`；新增 `DEVICE_COMMAND_TYPES` 白名单；新增 `RelayJson.deviceCommand` 与结果摘要 `summarizeCommandResult` | **接通远程控制**（此前管理端一个 command 方法都没有） |
| 2 | `model/Models.kt` | 新增 `DeviceCommand` 数据模型 + `commandTypeLabel()` 共享文案 | 同上 |
| 3 | `ui/DeviceScreen.kt` | 每个"已启用设备"卡片新增**远程控制区块**：三条指令按钮（查询电量/查最近短信/触发上报）+ 最近指令记录（状态、时间、结果摘要）+ 刷新按钮；已撤销设备显示"恢复后方可下发" | 补上缺失的 UI |
| 4 | `MainActivity.kt` | 新增 `sendDeviceCommand()`（二次确认 + 下发 + 乐观插入 + 刷新）、`loadDeviceCommands()`；接入设备页；登录后与创建设备后自动加载 | 接线 |
| 5 | `MainActivity.kt` | **批删按 500 分片**：`ids.chunked(BULK_DELETE_CHUNK).sumOf { client.bulkDeleteMessages(it) }` | 修"已加载 >500 条时 `require(size<=500)` 抛异常、一条都删不掉"（时间线容量 5000，UI 却承诺"删除全部 N 条"） |
| 6 | `app/build.gradle.kts` | `versionCode 9→10`、`versionName 1.0.8→1.0.9` | 同上 |

管理端远程控制区块的字段契约（已与后端逐条对齐）：
- 下发：`POST /api/v1/admin/devices/{id}/commands`，body `{"command_type": "<白名单>"}` → 201
- 列表：`GET  /api/v1/admin/devices/{id}/commands?limit=N` → 数组，元素含 `id/device_id/command_type/status/result/created_at/sent_at/completed_at`
- 状态映射：`pending` 待设备取走 / `sent` 已下发待回传 / `completed` 已完成
- 结果摘要与采集端 `DeviceCommandExecutor` 的回传结构一一对应：`battery_percent`+`charging` / `count`+`messages` / `queued`；设备侧未授权或无权限时统一是 `{"error":..., "message":...}` → 显示"设备返回失败：…"

---

## 四、验证情况（如实说明）

| 项目 | 状态 |
|---|---|
| 采集端 `:app:compileDebugKotlin` | ✅ BUILD SUCCESSFUL（仅既有 deprecation 警告） |
| 采集端 `clean testDebugUnitTest lintDebug assembleRelease` | ✅ **BUILD SUCCESSFUL in 7m 5s**（87 tasks；单测 + lintVital + 签名 APK 全跑） |
| 管理端 `:app:compileDebugKotlin` | ✅ BUILD SUCCESSFUL |
| 管理端 `clean testDebugUnitTest lintDebug assembleRelease` | ✅ **BUILD SUCCESSFUL**，输出签名 APK |
| **真机行为验证（磁贴长按/点击、电池优化跳转、远程指令往返）** | ❌ **未做** —— 本机没有连接设备，必须由你安装后实测 |

### 产物指纹（终检实测）

| 应用 | 版本 | 大小 | sha256 |
|---|---|---|---|
| 采集端 `io.smsrelay.collector` | **1.0.21 (code 22)** | 2,522,445 | `7f3232705076a8f37aa5af19685324791dbcb519769635cedac9c228ee1aa5a3` |
| 管理端 `io.smsrelay.manager` | **1.0.9 (code 10)** | 3,372,766 | `d6eac3f0b705ece897af3bbce31cb74ac6e187f796238e5742c33614d25a0b9e` |

**签名一致性已实测**：仓库内 40 个历史 APK（采集 1.0.8→1.0.20、管理 1.0.0→1.0.8）证书均为 `f6bfe7a6…`（DN=`CN=Android Debug`），与本次新构建的**完全一致** → 可直接覆盖安装，不会出现签名冲突。

**manifest 结构已实测**（`aapt2 dump xmltree`）：`QS_TILE_PREFERENCES` 确实挂在 `MainActivity`（源文件 L42-44），`TOGGLEABLE_TILE=true` 挂在 `RelayTileService`（源文件 L72-74）——没有被误挂到磁贴服务的 intent-filter 里。

**必须你真机确认的三点**：
1. 点"打开电池优化设置" → 应直接进系统"电池优化"列表页，**不弹框、不黑屏**；设为"不优化"后返回，状态行应显示"电池优化：已关闭"。
2. 磁贴：点击应能切到"已暂停"且**不再自动拉起**；长按应打开应用（不再是应用详情）。
3. 管理端设备页下发"查询电量" → 采集端授权"允许查询电量"后，约 30 秒内出现结果。

**若仍黑屏**：给我一次 `adb logcat`（过滤 `io.smsrelay.collector` 的 `AndroidRuntime`），我不再凭猜改第二次。

---

## 五、本轮**未做**的事（不糊弄你，明确列出来）

1. **平台侧（后端/网页）**：`otp_code` 字段缺失（`_serialize_message` 不输出，只有 WebSocket 推的带）、文档不实声称、`docs/DEPLOYMENT_AUDIT` 端口 8000→39200 —— 按你说的**留到下一轮**。
   > 注：管理端**已经在读** `otp_code`（`RelayAdminClient.RelayJson.message`），所以后端一补，管理端历史短信的验证码按钮就会自动出现，不用再改管理端。
2. **厂商自启动深链**（GKD 那种按 ROM 拼组件名的跳转表）：没做。原因是必须逐机型实测，贸然硬拼会黑屏或崩溃——正是你这轮最反感的那类问题。想做我可以按机型分批加，但每批都要你实测。
3. **远程控制结果加密 / 远程发短信**：安全与能力缺口（对照 SmsForwarder 的 RSA/SM4 与 `enableApiSmsSend`），需要设计决策，未动手。

---

# 第二轮修复（同日 18:31，含真机实测反馈）

用户在真机上验证了第一轮成果：**长按磁贴跳转已修好**（确认），但报出 2 个新问题。均已修复并**重新编译打包**。

## 六、磁贴"开了就关不回去"（真 bug，我第一轮改动引入的竞态）

**现象**：磁贴开启状态下点一下能关闭，**再点就开不起来**。

**根因（竞态）**：第一轮的 `onClick` 用"运行态"判断开关方向：

```kotlin
if (isRunning()) disable else enable          // isRunning() = RelaySwitch.isEnabled && serviceActive
...
BackgroundRuntime.start(this); syncTile()     // ← startForegroundService 是异步的
```

时序推演（与用户现象逐步吻合）：

| 步骤 | 开关 | `serviceActive` | 结果 |
|---|---|---|---|
| 点 1 | true→**false** | true→false | 关闭成功 ✅（用户说"能关闭"） |
| 点 2 | false→**true** | 仍为 false（服务还没起来） | `syncTile()` 读到 false → 磁贴仍显示"已暂停"，**看起来没生效** |
| 点 3 | true→**false**（因为 `isRunning()` 此时才为 true） | true | 用户想"再开一次"，实际把它**又关掉了** |

用户视角就是"点了开、开不起来"。**根因是拿异步的服务状态当同步的开关判据。**

**修法（`RelayTileService.kt` 重写）**：
1. **切换判据改为持久开关（用户意图）**，不再看 `serviceActive` —— 开关永远翻得动，不可能卡死：
   `val enable = !RelaySwitch.isEnabled(this)`。
2. **磁贴状态同步改为开关驱动**：`state = configured && enabled`，点完立即可见状态变化（不再有"点了没反应"的窗口）。
3. **标签区分"开"与"真的在跑"**，避免重蹈"显示运行中其实已死"：
   `未配置` / `已暂停` / `运行中`（开关开且服务在跑）/ `已开启`（开关开但服务没跑）。
4. **`onStartListening` 自愈**：开关为开但服务没在跑时，展开通知栏顺手补拉一次（best-effort）。
5. **整段 `runCatching` + 日志**：磁贴回调里抛异常会让整个磁贴失灵，绝不允许冒泡。

## 七、前台服务启动被系统拦截会直接崩溃（另一条真实故障路径）

**发现**：`SmsRelayForegroundService.onCreate` 里 `startForeground` **没有任何异常保护**，且 `serviceActive = true` 写在它**之后**：

```kotlin
ServiceCompat.startForeground(...)   // ← 第 25/32 行，无 try/catch
SmsRelayRuntime.serviceActive = true  // ← 第 34 行，前面抛了就永远执行不到
```

Android 12+ 有**后台启动前台服务限制**，从磁贴/后台拉起时可能抛
`ForegroundServiceStartNotAllowedException`。一旦抛出：异常从 `onCreate` 冒泡 → 服务进程崩溃 →
`serviceActive` 永远是 false → **磁贴"点了开不起来"，且没有任何提示**。

我查了官方豁免清单（`developer.android.com/.../fgs/restrictions-bg-start`），逐条核对：
- 清单里的"用户对与应用相关的界面元素执行操作"只举了**气泡 / 通知 / 微件 / activity**，**未列磁贴**；
- 与本项目相关的两条豁免是 —— **"用户为你的应用关闭了电池优化"** 和
  **"应用持有 SYSTEM_ALERT_WINDOW 且（target Android 15 时）当前有可见悬浮窗"**。
  而我们的悬浮窗是随服务走的：**暂停后没有悬浮窗 → 两条豁免都不成立**。

**修法**：
1. **`startForeground` 包 `runCatching`**：失败不再崩溃；置 `serviceActive = false`、`stopSelf()`。
2. **加一条可点击的兜底通知**（"系统限制了后台启动，点此打开应用即可恢复转发"）：
   *用户与通知交互本身就是系统认可的豁免场景*，点一下即可恢复。
3. 该通知走**独立的高可见度渠道** `relay_alerts`（IMPORTANCE_DEFAULT）——复用 LOW 的常驻渠道用户根本看不到。

> 这也顺带说明：**把"电池优化"设为"不限制"不只是省电问题**，它本身是系统承认的豁免条件之一，能实打实提高磁贴/后台拉起的成功率。

## 八、按钮合并（按你要求）

原有两个按钮在多数 ROM 上落到同一处，已合并为一个：

| 原 | 现 |
|---|---|
| "后台运行不受限制" + "打开应用详情（自行找到自启动/省电设置）" | **"打开省电/自启动设置（自行设为“不受限制”）"** |

行为：`APP_BATTERY_USAGE_SETTINGS`（省电策略页）→ 应用详情页 → 系统设置首页，逐级兜底、全程包异常。
`BackgroundRuntime.openAutoStartSettings` 已删除，`openUnrestrictedBackgroundSettings` 重命名为
`openBackgroundRestrictionSettings`（两个调用点同步更新，无残留引用）。

## 九、管理端短信页排版优化（你的截图）

**问题**：截图里"实时短信"标题 + 连接状态卡片 + 筛选卡片 + 消息管理卡片把屏幕吃掉大半，
短信记录只剩约一行高，**单条短信（含正文与多个按钮）根本看不全**。

**改法（`MessageScreen.kt` + `UiSupport.compactButton`）**：

| 区域 | 改前 | 改后 |
|---|---|---|
| 标题 | "实时短信"大标题（与页头重复） | **删除** |
| 连接状态 | 整张卡片（3 行） | **一行 13sp 小字** |
| 筛选 | 卡片 + "设备"/"SIM" 两行标签 + 两个大下拉 | **紧凑卡片 + 两个下拉并排一行**（15sp 紧凑适配器） |
| 消息管理 | 卡片 + 两个全宽按钮（2 行） | **紧凑卡片 + 两个按钮并排一行** |
| 短信记录列表 | 仅剩约 1 行 | **独占剩余全部高度**（weight=1） |
| 单条短信按钮 | 验证码 / 复制 / [已读][删除] 共 3 行 | 验证码单独一行；**复制 / 已读 / 删除 合并成一行**（13sp 紧凑按钮，仍约 40dp 可点高度） |

净效果：上方固定区由约 460dp 压到约 150dp，**列表可视高度约翻 2.5 倍**，单条短信可完整展示。

### 第二轮产物指纹（终检实测）

| 应用 | 版本 | 大小 | sha256 |
|---|---|---|---|
| 采集端 `io.smsrelay.collector` | **1.0.22 (code 23)** | 2,522,733 | `82e1ad7f22f9450e57a14fa50df20aff5ab8a7f1da713d13920f74aa31470531` |
| 管理端 `io.smsrelay.manager` | **1.0.10 (code 11)** | 3,373,122 | `c709d92e0a47bbbfe80259d89dacc0b8b3dad6e4c712f590a3fc8a644252cc30` |

构建：采集端 `clean + testDebugUnitTest + lintDebug + assembleRelease` → **BUILD SUCCESSFUL in 7m 49s**（87 tasks）；
管理端同流程 → **BUILD SUCCESSFUL in 6m 30s**（81 tasks）。
**dex 内容已逐个检索确认**（新文案全部 FOUND）：采集端 `打开省电/自启动设置`、`Relay转发已开启/已暂停/未配置`、
`系统限制了后台启动`、`Relay 转发未能恢复`；管理端 `批量删除当前列表`、`数据保留清理`、`复制短信`。
签名与历史版本一致（`f6bfe7a6…`），可直接覆盖安装。

### 第二轮请真机确认

1. 磁贴：点一下开（磁贴变亮、标签变"运行中"，通知栏出现常驻通知），**再点一下关，可反复来回切**。
2. 若出现"Relay 转发未能恢复"通知 → 说明系统拦了后台启动，**点该通知一次**即可恢复；
   同时建议把电池优化设为"不限制"（这本身就是系统认可的启动豁免，能根治）。
3. 主界面：只剩**一个**省电/自启动按钮；点它应能进设置页。
4. 管理端短信页：上方三个卡片变紧凑，**列表应能看到 2–3 条完整短信**（含正文与操作按钮）。
