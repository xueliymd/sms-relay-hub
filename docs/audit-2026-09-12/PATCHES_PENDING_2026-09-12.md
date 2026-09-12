# 待应用补丁清单 · 2026-09-12（采集端 / 管理端 / 后端）

> **状态：部分已应用（2026-09-12 17:0x），仍未编译验证。**
> 原因：本机无 Android SDK / JDK / `gradlew` / `local.properties`（已实测探测），无法编译任何 Android 改动。
> 每项都注明：改哪里、现状、怎么改、风险、如何验证。
>
> ### ✅ 本轮已落盘（源码已改，**未编译**，需你那边出 APK 验证）
> | 项 | 文件 | 说明 |
> |---|---|---|
> | 电池优化"选错变体 + 闪退" | `android/.../BackgroundRuntime.kt` | `openBatteryOptimizationSettings` 改为**页面优先**（列表页 → 应用专属请求页 → 应用详情页）；`startFirstResolvable` 改为**逐项可解析 + runCatching + 拉不起来继续试**（原写法异常会冒泡终止进程） |
> | 电池优化文案 | `android/.../MainActivity.kt` | 按钮 "关闭电池优化" → "打开电池优化设置（手动设为不限制）"；状态提示 "点下方按钮关闭" → "点下方按钮去设置"；首次引导弹窗第 2 条同步改写 |
> | 磁贴长按打开应用 | `android/app/src/main/AndroidManifest.xml` | 给 `MainActivity` 增加 `QS_TILE_PREFERENCES` intent-filter（**纯 XML，零编译风险**） |
>
> ### ⏳ 仍未应用（待你确认后再动）
> P0-1 的**磁贴可开可关 + 真实运行状态**（需动 6 个 Kotlin 文件，未编译风险较高）、P0-3 厂商自启动深链、P1-1/1-2/1-3/2。

---

## P0-0 【已应用】电池优化按钮（用户投诉 #1）—— 选错变体 + 会闪退

### 事实认定（用户 17:0x 二次澄清后修正）
用户原话：**"我原来逻辑可是快捷键打开这个系统自动电池优化页面，然后自己手动开启电池优化功能，GKD 项目上也是有这个的"**。即他要的是**方案①跳系统页面**，不是**方案②弹请求框**。

| 方案 | 意图 | 行为 |
|---|---|---|
| ① **跳页面**（用户要的） | `Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS`（不带 data） | 打开系统"电池优化"列表页 → 用户自己把本应用设为"不优化" |
| ② **弹对话框**（原实现） | `Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` + `data=package:xxx` | 直接弹"是否允许忽略电池优化？" |

两处缺陷：
- **A 行为选错**：原代码把方案②当首选，方案①只作失败回退（原 `BackgroundRuntime.kt:57-67`）。
- **B 会闪退**：原 `onFailure { context.startActivity(...) }` 的回退调用**未包 `runCatching`**；`startFirstResolvable` 也把 `startActivity` 放在异常保护之外。目标活动在本 ROM 上不可启动时（`SecurityException`/`ActivityNotFoundException`）异常冒泡 → **进程被终止 = "点一下黑屏闪一下就结束"**。

GKD 的对照实现（`gkd-app/src/main/kotlin/li/gkd/app/permission/PermissionState.kt`）：
```kotlin
val ignoreBatteryOptimizations by lazy {
    requestablePermissionState(
        name = "忽略电池优化权限",
        purpose = "用于降低后台服务被系统休眠或终止的概率",
        permission = PermissionLists.getRequestIgnoreBatteryOptimizationsPermission(),
        check = { app.powerManager.isIgnoringBatteryOptimizations(app.packageName) },
    )
}
```
即 GKD **先判状态、再引导用户去系统页**，而不是直接甩一个请求框。

### 已落盘的改动
**`android/.../BackgroundRuntime.kt`**
```kotlin
    fun openBatteryOptimizationSettings(context: Context) {
        val packageUri = Uri.parse("package:${context.packageName}")
        val intents = listOf(
            Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS),      // ① 页面优先
            Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, packageUri), // ② 兜底
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, packageUri),  // ③ 最终兜底
        )
        startFirstResolvable(context, intents)
    }

    private fun startFirstResolvable(context: Context, intents: List<Intent>) {
        val resolver = context.packageManager
        for (intent in intents) {
            val launchable = intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (resolver.resolveActivity(launchable, PackageManager.MATCH_DEFAULT_ONLY) == null) continue
            if (runCatching { context.startActivity(launchable) }.isSuccess) return
        }
    }
```
**`android/.../MainActivity.kt`**
- 按钮 `"关闭电池优化"` → `"打开电池优化设置（手动设为不限制）"`
- 状态 `"…（点下方按钮关闭）"` → `"…（点下方按钮去设置）"`
- 首次引导弹窗第 2 条 `"2. 关闭电池优化"` → `"2. 打开电池优化设置，把本应用设为“不优化/不受限制”"`

### 验证方式（必须真机）
1. 点按钮 → 应直接进入系统"电池优化"列表页（不是弹请求框、不黑屏）。
2. 在列表页把本应用设为"不优化"，返回 App → 状态栏应显示"电池优化：已关闭"。
3. 若仍黑屏：抓一次 `adb logcat` 看是否有 `SecurityException`/`ActivityNotFoundException`；**不要再凭猜改**。

---

## P0-1 磁贴（用户投诉 #2）—— 三处缺陷

### 事实认定
| 现象 | 用户感受 | 根因 | 证据 |
|---|---|---|---|
| 点击不能关闭 | "点击也不能关闭" | `onClick` 只有 `revive()`，无 stop 路径 | `RelayTileService.kt:28-42` |
| 状态显示"运行中"但实际已死 | （用户未提，但同类） | `syncTile` 用 `config != null` 判定，非服务真实运行 | `RelayTileService.kt:53-60` |
| 长按跳应用详情 | "长按打开是跳转应用详情，根本不是打开应用" | 未声明 `QS_TILE_PREFERENCES` Activity → 系统默认行为 | `AndroidManifest.xml:50-59` |

> **重要澄清（官方文档已核实）**：长按磁贴弹出"应用详情"是 Android 对**所有** TileService 的**默认行为**，不是我们写错了代码。要改成打开应用，必须**显式**声明一个带 `android.service.quicksettings.action.QS_TILE_PREFERENCES` 的 Activity（AOSP `TileService.ACTION_QS_TILE_PREFERENCES`）。我们**漏做了这一步**——所以用户的主观判断是对的：这个功能确实没做全。

### 改动 1：磁贴可开可关 + 反映真实运行状态（Kotlin 4 个文件）

**(1) `android/.../SmsRelayRuntime.kt`** — 在 `cachedConfig` 之后（约 L10 后）新增运行标志：
```kotlin
    /** 前台服务是否正在运行；由 SmsRelayForegroundService 的 onCreate/onDestroy 维护。
     *  进程被杀时随进程一起消失 → 磁贴自然显示"未运行"，符合预期。 */
    @Volatile var serviceActive: Boolean = false
```

**(2) `android/.../SmsRelayForegroundService.kt`**
- `onCreate()` 末尾（L34 `attachAppOverlayIfPossible()` 之后）加：
```kotlin
        SmsRelayRuntime.serviceActive = true
```
- `onDestroy()`（L64-67）中 `super.onDestroy()` 之前加：
```kotlin
        SmsRelayRuntime.serviceActive = false
```

**(3) `android/.../KeepAliveWatchdog.kt`** — 补一个取消方法（放在 `enqueuePeriodicWorker` 之后）：
```kotlin
    /** 关闭保活：与 enqueuePeriodicWorker 配对，供磁贴"关闭"路径调用。 */
    fun cancel(context: Context) {
        WorkManager.getInstance(context.applicationContext)
            .cancelUniqueWork(PERIODIC_WORK_NAME)
    }
```

**(4) `android/.../BackgroundRuntime.kt`** — 补一个停止方法（放在 `start` 之后）：
```kotlin
    /** 停止前台服务（磁贴"关闭"路径）。stopService 后 START_STICKY 不会再拉起。 */
    fun stop(context: Context) {
        context.stopService(Intent(context, SmsRelayForegroundService::class.java))
    }
```

**(5) `android/.../RelayTileService.kt`** — `onClick` 与 `syncTile` 改为按真实运行状态切换：
```kotlin
    override fun onClick() {
        super.onClick()
        val configured = SmsRelayRuntime.config(this) != null
        if (configured && SmsRelayRuntime.serviceActive) {
            // 正在运行 → 关闭
            BackgroundRuntime.stop(this)
            KeepAliveWatchdog.cancel(this)
            SmsRelayRuntime.serviceActive = false
        } else if (configured) {
            // 已配置但未运行 → 启动
            BackgroundRuntime.start(this)
            KeepAliveWatchdog.enqueuePeriodicWorker(this)
        }
        syncTile()
    }

    private fun syncTile() {
        val active = SmsRelayRuntime.serviceActive && SmsRelayRuntime.config(this) != null
        qsTile?.let { tile ->
            tile.state = if (active) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
            tile.label = when {
                active -> "Relay转发运行中"
                SmsRelayRuntime.config(this) != null -> "Relay转发已停止"
                else -> "Relay转发未配置"
            }
            tile.updateTile()
        }
    }
```
> 注：`armKeepAlive()`（L44-51）在 `onStartListening`/`onTileAdded` 里会登记看门狗——**这与"关闭"语义冲突**：用户点关闭后，只要展开通知栏就会把看门狗重新登记上，"关闭"不彻底。若要语义干净，`armKeepAlive()` 应加 `if (SmsRelayRuntime.serviceActive)` 条件，或干脆删除该调用（看门狗改由前台服务与无障碍服务负责登记）。

### 改动 2：长按磁贴改为打开应用（最小改法，只动 manifest）

**`android/app/src/main/AndroidManifest.xml`** — 给 MainActivity 增加一个 intent-filter（L31-36 的 activity 内追加）：
```xml
    <activity android:name=".MainActivity" android:exported="true">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
      <!-- 长按磁贴时打开本应用，覆盖系统默认的"应用详情"行为 -->
      <intent-filter>
        <action android:name="android.service.quicksettings.action.QS_TILE_PREFERENCES" />
      </intent-filter>
    </activity>
```
并在 `RelayTileService` 的 service 节点内（L55-58 之间）补两段 meta-data：
```xml
      <meta-data android:name="android.service.quicksettings.TOGGLEABLE_TILE" android:value="true" />
      <meta-data android:name="android.service.quicksettings.ACTIVE_TILE" android:value="true" />
```
- `TOGGLEABLE_TILE`：声明本磁贴是两态开关（官方推荐，改善 a11y）。
- `ACTIVE_TILE`：声明磁贴自己在进程内跟踪状态（我们正是这么做的），使系统按需绑定而非常驻。

**风险与真机预期（必须实测，不要凭文档下结论）**：
- Android 12 及以下：长按 → 直接打开 MainActivity。
- Android 13+：长按仍会先弹系统的"磁贴详情"底部弹窗，其中提供进入我们声明的设置的入口；**不会**像 <=12 那样直接进 App。若你要求 13+ 也"长按直达应用"，需要额外用 `startActivityAndCollapse()` 等变通，且部分 ROM 不支持——这属于平台限制，需在目标机型上确认后再定方案。
- 改动 2 只加 manifest 条目，不改 Kotlin，编译风险最低。

---

## P0-2 采集端文案与行为不符（1 行）

**`MainActivity.kt:158`**
- 现状：`text = "开启自启动（厂商设置）"`
- 实际：`BackgroundRuntime.openAutoStartSettings`（`:91-97`）**只打开通用应用详情页**，不会跳到厂商自启动开关。
- 改为：`text = "打开应用详情（手动开启自启动）"`
- 风险：无（纯字符串）。若同时应用 P0-3，则可在深链成功时再显示"厂商设置"。

---

## P0-3 厂商自启动深链（可选，需真机逐 ROM 验证）

**`BackgroundRuntime.kt:91-97` `openAutoStartSettings`** 增加"先试厂商组件、失败回退详情页"：
```kotlin
    private val VENDOR_AUTOSTART = listOf(
        // 小米 / Redmi
        "com.miui.securitycenter" to "com.miui.permcenter.autostart.AutoStartManagementActivity",
        // 华为 / 荣耀
        "com.huawei.systemmanager" to "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
        // OPPO / realme
        "com.coloros.safecenter" to "com.coloros.safecenter.permission.startup.StartupAppListActivity",
        // vivo / iQOO
        "com.vivo.permissionmanager" to "com.vivo.permissionmanager.activity.BgStartUpManagerActivity",
        // 三星
        "com.samsung.android.lool" to "com.samsung.android.sm.ui.battery.BatteryActivity",
    )

    fun openAutoStartSettings(context: Context) {
        val packageUri = Uri.parse("package:${context.packageName}")
        for ((pkg, cls) in VENDOR_AUTOSTART) {
            val intent = Intent().setComponent(android.content.ComponentName(pkg, cls))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (context.packageManager.resolveActivity(intent, PackageManager.MATCH_DEFAULT_ONLY) != null) {
                if (runCatching { context.startActivity(intent) }.isSuccess) return
            }
        }
        // 全部失败 → 回退通用应用详情页（保留原行为）
        startFirstResolvable(
            context,
            listOf(
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).setData(packageUri),
                Intent(Settings.ACTION_SETTINGS),
            ),
        )
    }
```
**风险（高，必须逐机型实测）**：厂商类名随 ROM 版本变化，写错会"点了没反应"或闪退（已用 `resolveActivity` + `runCatching` 双重兜底）。**不建议一次性铺开**，建议先只加你手上真机对应的那一两个厂商，确认可用再扩。
**背景**：这是与 GKD 的主要差距——GKD 有完整厂商跳转表，我们刻意保守（`BackgroundRuntime.kt:85-90` 注释说明理由）。国产 ROM 上"关闭电池优化"点 AOSP 白名单往往不够，厂商自启动才是关键，这是保活实效的主要可改进点。

---

## P1-1 管理端批量删除 >500 条失效

**`manager-android/.../RelayAdminClient.kt:209-214`**（`require(messageIds.size <= 500)`）
**`manager-android/.../MainActivity.kt:742-755`**（`bulkDeleteMessages` 取 `timeline.items()`，时间线容量 5000，见 `MainActivity.kt:51`）

**改法（在 `bulkDeleteMessages` 内按 500 切片）**：
```kotlin
    private fun bulkDeleteMessages() {
        val loaded = timeline.items()
        if (loaded.isEmpty()) return
        confirm("将删除当前列表已加载的全部 ${loaded.size} 条短信，且不可恢复。") {
            runAdminOp {
                loaded.map { it.id }.chunked(500).forEach { chunk ->
                    client.bulkDeleteMessages(chunk)   // 每片 <=500，规避 require
                }
                timeline.removeAll(loaded.map { it.id }.toSet())
            }
        }
    }
```
（`client` / `timeline` / `confirm` / `runAdminOp` 的实际成员名以文件为准，需对照现有实现微调。）
**风险**：中。切片后是多次请求，中途失败会部分删除；建议加进度提示。
**替代方案**：后端放宽上限或提供"按时间/设备范围清理"端点，更彻底但动后端。

---

## P1-2 后端 REST 短信缺 `otp_code`

**`backend/app/api/relay.py:662-687`**（`_serialize_message` 不输出 `otp_code`；只有 WS 路径 `_publish_admin_message` 在 `:694` 补）
→ 实时推送的短信有"验证码"按钮，**历史拉取/断线补拉的没有**。

**改法**：在 `_serialize_message` 的返回字典里补上 `otp_code`（取值需与 WS 路径一致，注意"已领取/已过期"状态处理），使 REST 与 WS 对齐。
**验证**：这是后端改动，可用现有 pytest 覆盖（`backend/tests/`）；但本机沙箱 pytest 进程会被中断，需在正常环境跑。

---

## P1-3 采集端 `Thread.sleep` 阻塞协程

**`KeepAliveWorker.kt:30`** 在 `suspend doWork()` 内调用 `AccessibilityAutoToggle.restart`，其内部有 `Thread.sleep(1000)`（`AccessibilityAutoToggle.kt:74`）→ 阻塞 WorkManager 线程。
**改法**：改用 `kotlinx.coroutines.delay`（把 restart 的等待段改成挂起函数），或在 Worker 内用 `withContext(Dispatchers.IO)` 包裹。
**风险**：低-中，需确认 coroutines 依赖已在（`android` 模块应已引入 WorkManager 的协程支持）。

---

## P2 悬浮窗"双保险"实际只有一层

**`KeepAliveOverlay.kt:20-46`**：`attached` 是单例标志，`acquire()` 若已挂载则直接返回 true。
→ 前台服务先挂 `APP_WINDOW_TYPE` 成功后，无障碍服务再调 `acquire(..., accessibilityWindowType())` 会**直接返回、不加窗口**，所谓"两层冗余"名存实亡。
**改法**：把状态改为"按 windowType 分别记录"（例如 `MutableSet<Int>` 记录已挂载的 windowType），每种类型各挂一个。
**风险**：低。但需真机确认加两个 window 不会被 ROM 判定异常。

---

## 附：本清单对应的证据文档
- 采集端全量复审（含磁贴/电池优化根因、与 GKD/SmsForwarder 对比）：`audit2_collector.md`
- 管理端全量复审（含批删 500 / otp_code / 远程控制 UI 缺失三条定论）：`audit2_manager.md`
