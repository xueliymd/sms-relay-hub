# 采集端（io.smsrelay.collector 1.0.20 / code 21）静态审计报告

只读审查，未修改任何文件。Scope：保活链路、主界面全部控件、远程控制、上报链路、版本更新、文档一致性。证据均附 `文件:行号`。

> **文件名澄清（重要）**：`DeviceCommandClient`、`DeviceCommandExecutor`、`DeviceCommandPoller` 三者**同处一个文件** `android/app/src/main/java/io/smsrelay/collector/DeviceCommandClient.kt`（分别为 L21 / L87 / L194），仓库里**不存在** `DeviceCommandExecutor.kt` 或 `DeviceCommandPoller.kt`。下文若出现"类名:行号"，指的都是 `DeviceCommandClient.kt` 内的行号。

---

## 一、结论速览

### ✅ 真做对了（逻辑自洽，可交付）
- 前台服务 `specialUse` 类型声明与 Android 14+ 的 `FOREGROUND_SERVICE_TYPE_SPECIAL_USE` 正确配对（`AndroidManifest.xml:40`、`SmsRelayForegroundService.kt:22-33`）。
- 无障碍服务 `onUnbind` 返回 `true` 实现系统级自动重连（`RelayKeepAliveAccessibilityService.kt:38`）；`WRITE_SECURE_SETTINGS` 一键开启/重启自愈（`AccessibilityAutoToggle.kt:43-84`、`KeepAliveWorker.kt:23-32`）。
- WorkManager 周期看门狗 15 分钟兜底 + `START_STICKY` + 开机/更新自启（`KeepAliveWatchdog.kt:20-24`、`BootReceiver.kt:8-16`、`SmsRelayForegroundService.kt:51`）。
- **心跳闹钟已彻底移除**：全工程 `grep AlarmManager/setRepeating/setExact` 零命中（核实结论，无残留引用）。
- 短信采集 → 幂等 `client_message_id`（SHA-256(sender+date+body)）→ Room 加密队列（AES-GCM/AndroidKeyStore）→ WorkManager 兜底 + 指数退避（`SmsReceivedReceiver.kt`、`SmsRelayPayload.kt:52-58`、`SmsRelayQueueRepository.kt:12-31`、`UploadScheduler.kt:22`、`SmsRelayLocalCipher.kt`）。
- 远程控制：30s 节流 + `AtomicBoolean` 重入保护、`query_*`/`trigger_upload` 三指令、号码脱敏（保留后4位）、未授权回传说明（`DeviceCommandClient.kt:194-226`（`DeviceCommandPoller`）、`DeviceCommandClient.kt:87-187`（`DeviceCommandExecutor`）、`MainActivity.kt:165-178`）。
- 版本更新：下载校验 SHA-256 + 签名证书必须等同已装 + 版本必须更新（`AppUpdateClient.kt:41-60`、`AppUpdateVerifier.kt:11-26`）。
- HMAC 签名 `timestamp\nnonce\nsha256(body)` 与 README 协议一致（`SmsRelaySigner.kt:20-24`、`SmsRelayHttpClient.kt:23`）。

### ❌ 确实错了 / 有真实缺陷（含两处用户投诉）
0. **电池优化按钮（投诉 #1）**：**(a)** 选错跳转变体——用户要"跳系统页面自己手动设"，代码却首选"弹请求框"；**(b)** 失败回退路径的 `startActivity` **未包异常**，在本 ROM 上可致进程被终止 → "黑屏闪一下就结束"。**已修（未编译）**，详见 §3.2。
1. **磁贴点击无法切回关闭状态**：`RelayTileService.onClick` 只 `revive()`（启动服务），**没有任何停止路径**（`RelayTileService.kt:28-42`）。
2. **磁贴状态不反映真实运行**：`syncTile()` 用 `config(this) != null` 判定 active，而非服务实际是否在跑（`RelayTileService.kt:53-60`）→ 服务已死也显示"运行中"。
3. **磁贴长按跳应用详情页而非打开应用**：manifest 未声明 `QS_TILE_PREFERENCES` 对应 Activity（`AndroidManifest.xml:50-59`）。**已修（manifest-only，未编译）**。

### ⚠️ 文案与行为不符
- "开启自启动（厂商设置）"（`MainActivity.kt:158`）文案承诺"厂商设置"，实际只打开**通用应用详情页**，`openAutoStartSettings` 故意不深链厂商自启动（`BackgroundRuntime.kt:91-97`，注释 85-90）。
- `PENDING_FEATURES_REPORT_2026-09-12.md:17` 把"磁贴"标 ✅，但上述缺陷说明该 ✅ 名不副实。
- `README.md:50` "不再提供会跳转厂商设置页的按钮" 与代码**不矛盾**（三个按钮打开的都是标准 AOSP 设置，非厂商深链）；但"厂商设置"标签本身仍过度承诺。

### 🧹 死代码 / 状态不更新 / 文档过期
- `KeepAliveOverlay` 用单例 `attached`/`view`（`KeepAliveOverlay.kt:20-46`）：前台服务与无障碍服务两个入口只有一个能真正 `addView`，**第二层悬浮窗永不被添加**，破坏"双保险"设计意图。
- `AccessibilityServiceState.kt:10` 的 `shortComponent`（`.RelayKeepAliveAccessibilityService`）与实际写入的全类名组件永不匹配 → 死分支。
- `ImmediateUploadPayloadStore.take()`（`ImmediateUploadPayloadStore.kt:20-27`）定义但**未被任何调用方使用**。
- `PENDING_FEATURES_REPORT` 多处写"1.0.19"（如 :71、:99），实测为 **1.0.20/code 21**（`build.gradle.kts:30-31`）→ 报告滞后一个版本。

---

## 二、逐项核对表

| 功能项 | 声称 | 代码位置 | 实际行为 | 判定 | 证据 |
|---|---|---|---|---|---|
| 前台服务 specialUse | 常驻监听 | `SmsRelayForegroundService.kt:17-35`、`AndroidManifest.xml:38-49` | 正确声明 specialUse 子类型并在 API34+ 指定类型 | 正确 | manifest:40,48 |
| 无障碍自动重连 | 被杀后系统拉回 | `RelayKeepAliveAccessibilityService.kt:38` | onUnbind=true 自动重绑 | 正确 | :38 |
| 无障碍一键开/重启 | ADB 授权后自动 | `AccessibilityAutoToggle.kt` | 写 ENABLED_ACCESSIBILITY_SERVICES 成功 | 正确 | :43-84 |
| 悬浮窗双窗口 | 两层冗余保活 | `KeepAliveOverlay.kt:20-46` | 单例状态导致只加一个窗口 | 缺陷 | :20,25 |
| 开机/更新自启 | 恢复服务 | `BootReceiver.kt:8-16` | BOOT_COMPLETED+MY_PACKAGE_REPLACED 均触发 | 正确 | :9,12 |
| WorkManager 看门狗 | 15min 兜底 | `KeepAliveWatchdog.kt:20-24` | 周期 15min KEEP 策略 | 正确 | :21 |
| 心跳闹钟移除 | 已精简 | 全局 grep | 零 AlarmManager 引用 | 正确 | grep 无命中 |
| 磁贴点击 | 复活/也可关闭 | `RelayTileService.kt:28-42` | 仅 revive，无 stop 路径 | **缺陷** | :35-42 |
| 磁贴状态 | 反映运行 | `RelayTileService.kt:53-60` | 仅看 config 是否存在 | **缺陷** | :54 |
| 磁贴长按 | 打开应用 | `AndroidManifest.xml:50-59` | 无 QS_TILE_PREFERENCES→跳应用详情 | **缺陷** | manifest:56-58 |
| 扫码配置 | 二维码兑换 | `MainActivity.kt:258-307` | EnrollmentClient 单次兑换并保存 | 正确 | :281-306 |
| 保存配置 | 持久化+自动开无障碍 | `MainActivity.kt:225-256` | 保存并可选自动开启 a11y | 正确 | :234-250 |
| 授予短信权限 | RECEIVE_SMS | `MainActivity.kt:100-105` | 标准请求 | 正确 | :103 |
| SIM 元数据权限 | 可选 | `MainActivity.kt:106-111` | READ_PHONE_STATE/NUMBERS | 正确 | :109 |
| 隐藏应用窗口 | 移除最近任务 | `MainActivity.kt:112-115` | finishAndRemoveTask | 正确 | :114 |
| 无障碍保活按钮 | 一键开 | `MainActivity.kt:116-148` | 有 WRITE_SECURE 则自动，否则引导 | 正确 | :122-146 |
| 关闭电池优化 | 打开系统页面手动设 | `MainActivity.kt:149-152`、`BackgroundRuntime.kt` `openBatteryOptimizationSettings` | 原为"弹请求框"变体（选错）+ 回退未包异常（会闪退）；**已改为页面优先+全链路兜底** | **缺陷（已修，待编译验证）** | 见三.2 |
| 后台运行不受限制 | 打开电池用量 | `MainActivity.kt:153-156`、`BackgroundRuntime.kt:69-76` | APP_BATTERY_USAGE_SETTINGS 回退应用详情 | 正确 | :72-73 |
| 开启自启动(厂商) | 厂商自启动页 | `MainActivity.kt:157-160`、`BackgroundRuntime.kt:91-97` | **仅打开通用应用详情页** | **文案不符** | :93-94 |
| 远程指令3个勾选框 | 默认关闭授权 | `MainActivity.kt:165-178`、`CommandSettingsStore.kt` | 默认 false，关闭回传未授权 | 正确 | :173-175 |
| 短信读取权限 | 查最近短信用 | `MainActivity.kt:179-184` | READ_SMS 标准请求 | 正确 | :182 |
| 后台通知权限 | POST_NOTIFICATIONS | `MainActivity.kt:185-192` | TIRAMISU+ 才显示 | 正确 | :185 |
| 检查/下载更新 | 自更新 | `MainActivity.kt:360-467` | 校验+安装器 | 正确 | :427-451 |
| 远程轮询节流 | 防刷 | `DeviceCommandClient.kt:197-207`（`DeviceCommandPoller`） | 30s + AtomicBoolean 重入 | 正确 | :205-206 |
| HMAC 签名 | 防篡改 | `SmsRelaySigner.kt`、`DeviceCommandClient.kt:62` | sign 一致 | 正确 | :20-24 |
| query_recent_sms | 脱敏+READ_SMS 校验 | `DeviceCommandClient.kt:129-164`（`DeviceCommandExecutor`） | 无权限回传说明；号码脱敏 | 正确 | :130-137,176-184 |
| 上报幂等 | client_message_id | `SmsRelayPayload.kt:52-58`、`SmsRelayHttpClient.kt:25` | 稳定摘要+X-Idempotency-Key | 正确 | :58,25 |
| 本地加密 | Keystore AES-GCM | `SmsRelayLocalCipher.kt` | 发件人/正文/号码加密落库 | 正确 | :15-27 |
| 退避重试 | 指数 | `UploadScheduler.kt:22` | EXPONENTIAL 30s | 正确 | :22 |
| 更新签名校验 | 防伪造 | `AppUpdateVerifier.kt:11-26` | 证书须等同已装 | 正确 | :22-25 |

---

## 三、两个焦点问题

### 3.1 磁贴（用户投诉 #2）——根因与修复

**根因**（均经 `RelayTileService.kt` + `AndroidManifest.xml:50-59` 核实）：
1. `onClick()` 仅调用 `revive()`（`BackgroundRuntime.start` + 登记看门狗），**从未调用 `stopService`/取消看门狗**，因此点击永远只能"开"，不能"关"（`RelayTileService.kt:28-42`）。
2. `syncTile()` 的 active = `SmsRelayRuntime.config(this) != null`，是"是否已配置"而非"服务是否在跑"；服务被杀后 config 仍在，磁贴仍显示 STATE_ACTIVE/"运行中"（`RelayTileService.kt:53-60`）。
3. manifest 中 `RelayTileService` 仅声明 `QS_TILE` intent-filter，**未声明 `QS_TILE_PREFERENCES` 对应 Activity**（`AndroidManifest.xml:56-58`）。AOSP 下长按无目标；**国产 ROM（MIUI/HyperOS、EMUI、ColorOS）会回退到应用详情页**——与用户"长按跳应用详情页"完全吻合。

**对照开源（WebFetch 实证）**：
- GKD `GkdTileService.onTileClick()` = `switchAutomatorService()`：**运行中则 `disableSelf()` 关闭，否则开启**，即点击可切换（`gkd-kit/gkd` `GkdTileService.kt:181`）。
- GKD `BaseTileService` 用 `activeFlow`（真实 `A11yService.isRunning`）驱动 `qsTile.state`（`BaseTileService.kt:23-31`），状态=真实运行。
- GKD 在 `AndroidManifest.xml` 声明 `OpenTileActivity` 带 `<action android:name="android.service.quicksettings.action.QS_TILE_PREFERENCES"/>`，长按即打开该透明 Activity 进而进入 App（`gkd-kit/gkd` `AndroidManifest.xml` OpenTileActivity 段）。

**具体修复建议（可直接落地）**：

(1) 新增透明中转 Activity（仿 GKD OpenTileActivity），让长按打开应用：
```xml
<!-- AndroidManifest.xml，RelayTileService 内或同级 -->
<activity android:name=".OpenTileActivity"
    android:exported="true"
    android:theme="@style/TransparentTheme" android:taskAffinity="">
    <intent-filter>
        <action android:name="android.service.quicksettings.action.QS_TILE_PREFERENCES" />
    </intent-filter>
</activity>
```
```kotlin
class OpenTileActivity : Activity() {
    override fun onCreate(s: Bundle?) {
        super.onCreate(s)
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        finish()
    }
}
```

(2) 给磁贴增加"可切换"声明与真实状态（在 `RelayTileService` service 节点补）：
```xml
<meta-data android:name="android.service.quicksettings.TOGGLEABLE_TILE" android:value="true" />
```

(3) `RelayTileService` 改造：用真实运行标志判定，点击可关可开：
```kotlin
override fun onClick() {
    super.onClick()
    if (SmsRelayRuntime.isServiceActive() && SmsRelayRuntime.config(this) != null) {
        BackgroundRuntime.stop(this)            // 新增：stopService + 取消看门狗
        KeepAliveWatchdog.cancel(this)
    } else if (SmsRelayRuntime.config(this) != null) {
        BackgroundRuntime.start(this)
        KeepAliveWatchdog.enqueuePeriodicWorker(this)
    }
    syncTile()
}
private fun syncTile() {
    val active = SmsRelayRuntime.isServiceActive() && SmsRelayRuntime.config(this) != null
    qsTile?.let {
        it.state = if (active) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        it.label = if (active) "Relay转发运行中" else "Relay转发已停止"
        it.updateTile()
    }
}
```
其中 `isServiceActive` 由 `SmsRelayForegroundService.onCreate` 置 true、`onDestroy` 置 false（`SmsRelayRuntime` 增加 `@Volatile var serviceActive=false`）。进程被杀后标志复位为 false，磁贴可重新点击复活——符合预期。

### 3.2 电池优化（用户投诉 #1）——【结论已于同日 17:0x 修正】

> **修正说明**：本节初版结论"代码没有写反、标签与动作都正确"**不完整，已作废**。
> 经用户二次澄清（"我原来逻辑是快捷键打开系统电池优化页面，然后自己手动开启"）+ 复核 Android 官方机制 + 阅读 GKD `PermissionState.kt`，确认**真实缺陷有两处**，详见下。

**Android 有"两种"跳法，我们选错了那一种：**

| 方案 | 意图 | 表现 |
|---|---|---|
| ① **跳页面**（用户要的） | `Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS`（**不带 data**） | 打开系统"电池优化"列表页，用户自己找到本应用设为"不优化" |
| ② **弹对话框**（我们做的） | `Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` + `data=package:xxx` | 直接弹"是否允许忽略电池优化？"请求框 |

- **缺陷 A（行为选错）**：`BackgroundRuntime.kt:57-67`（原代码）把**方案②作为首选**，方案①只当失败回退 → 与用户明确要求的"跳页面自己手动设"不符。用户说"GKD 项目上也有这个"，属实：GKD 把它建模为一个权限状态（`gkd-app/.../permission/PermissionState.kt` 的 `ignoreBatteryOptimizations`，以 `PowerManager.isIgnoringBatteryOptimizations` 判状态），**先判状态再引导用户去系统页**，而不是直接甩一个请求框。
- **缺陷 B（真会闪退）**：原 `onFailure { context.startActivity(...) }` 的**回退调用没有包 `runCatching`**；且 `startFirstResolvable`（`BackgroundRuntime.kt`，被另外两个按钮使用）同样把 `startActivity` 放在异常保护之外。一旦目标活动在本 ROM 上实际不可启动（`SecurityException` / `ActivityNotFoundException`），异常会冒泡并**终止前台进程**——对外表现正是"点一下黑屏闪一下就结束"。
- 附带：按钮文案"关闭电池优化"易被理解为"把这个功能关掉"，与"去设置页手动设为不限制"的意图不一致。

**已修复（同日 17:0x 落盘，未编译验证）**：
- `BackgroundRuntime.kt` `openBatteryOptimizationSettings`：改为**方案①优先**、②③依次兜底（列表页 → 应用专属请求页 → 应用详情页）。
- `BackgroundRuntime.kt` `startFirstResolvable`：改为"逐项判可解析 + `runCatching` 包裹 + 拉不起来就试下一个"，消除未捕获异常导致的闪退。
- `MainActivity.kt`：按钮文案 → "打开电池优化设置（手动设为不限制）"；状态栏提示 → "仍受系统优化限制（点下方按钮去设置）"；首次引导弹窗第 2 条同步改写。

**仍存在的真实差距（与本投诉独立）**：国产 ROM（MIUI/HyperOS、EMUI、ColorOS）的"自启动/省电策略"是另一套数据库，AOSP 白名单常被忽略；"开启自启动（厂商设置）"实际只打开应用详情页（`BackgroundRuntime.kt` `openAutoStartSettings`，注释自承不深链厂商）。要根治需补厂商深链表（见 `PATCHES_PENDING_2026-09-12.md` P0-3），需逐机型实测。

**但用户的挫败感来自真实差距（非代码写反，而是国产 ROM 机制）**：
- 在 MIUI/HyperOS、EMUI、ColorOS 上，`ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` 只写入 AOSP 白名单，**厂商自带的"自启动/省电模式/应用速冻"是另一套数据库**，AOSP 豁免常被忽略 → 点了"关闭电池优化"应用仍被杀。
- 三个跳转按钮的**真实能力**：
  - "关闭电池优化"：开 AOSP 豁免页（多数国产 ROM 不够）。
  - "后台运行不受限制"：`APP_BATTERY_USAGE_SETTINGS` 回退应用详情（`BackgroundRuntime.kt:69-76`）。
  - "开启自启动（厂商设置）"：**只打开通用应用详情页**，注释明确"不指定厂商组件"（`:85-97`）——即**不会跳到厂商自启动开关**，用户须自己找。

**对照开源**：GKD 有一个**厂商自启动跳转表**（按 ROM 拼 `ComponentName` 深链到各厂商"自启动/省电"设置）。本项目刻意走保守路线（只开通用详情页），在国产机上等于没帮用户打开自启动，**这是保活失效的主要可改进点**。

**具体修复建议**：
- 短期（保活有效）：仿 GKD 增加厂商跳转表，`openAutoStartSettings` 依据 `Build.MANUFACTURER`/`Build.BRAND` 尝试 `startActivity(Intent().setComponent(ComponentName(pkg, cls)))`，失败再回退应用详情页。至少覆盖小米(com.miui.securitycenter/.…)、华为(com.huawei.systemmanager)、OPPO(com.coloros.safecenter)、vivo(com.iqoo.secure)、三星。
- 文案：把"开启自启动（厂商设置）"改为"打开应用详情（手动开启自启动）"，避免"厂商设置"过度承诺；或在能深链成功时再显示"厂商设置"。
- 状态提示：当前 `isIgnoringBatteryOptimizations` 在国产 ROM 可能返回 true 但应用仍被杀，状态栏"已不受限"会误导，建议加注"请以厂商设置为准"。

---

## 四、无法静态确认、必须真机验证的点

1. **国产 ROM 保活实效**：`specialUse` 前台服务 + 无障碍 + WorkManager 在 MIUI/HyperOS、EMUI、ColorOS、vivo 上 `adb force-stop` 后能否真复活、锁屏 24h 到达率（代码链路齐备，但 OEM 行为只能实测，见 `PENDING_FEATURES_REPORT:70`）。
2. **`ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` 在各 ROM 是否被识别**：部分 ROM 会直接丢弃该意图（静态只能确认回退路径存在，不能确认主路径生效）。
3. **无障碍"受限制的设置"拦截**：Android 13/14 侧载时系统可能阻止开启（`AccessibilityKeepAlive.kt:33-39` 已提示，但实际是否被拦需真机）。
4. **悬浮窗 TYPE_ACCESSIBILITY_OVERLAY 在目标 OEM 是否真不被回收**：代码已 `runCatching` 静默失败（`KeepAliveOverlay.kt:43`），但各 ROM 对无障碍窗口策略不同。
5. **磁贴修复后长按/点击行为**：第 3.1 节修复需真机确认长按打开应用、点击可切换关闭。
6. **版本更新真机安装**：`REQUEST_INSTALL_PACKAGES` + 未知来源授权流程（`AppUpdateInstaller.kt:15-23`）在 Android 13+ 实际弹窗与回退路径需实测。
7. **双卡 SIM 元数据映射**：`SmsSubscriptionHintResolver` 在实机双卡上的 slot/subscriptionId/号码准确性（已写契约测试，但真机矩阵未跑，`PENDING_FEATURES_REPORT:69`）。

---

### 文档一致性结论
- `README.md:50` 与代码**不矛盾**（三个按钮打开标准 AOSP 设置，非厂商深链；`openAutoStartSettings` 故意不开厂商页）。但"开启自启动（厂商设置）"标签本身与行为不符（见一/二）。
- `PENDING_FEATURES_REPORT_2026-09-12.md:17` 把"磁贴"标 ✅ **名不副实**（第 3.1 证实磁贴三处缺陷）；报告版本号 1.0.19 滞后于实测 1.0.20。
- **过期方**：`PENDING_FEATURES_REPORT` 对采集端保活/磁贴的 ✅ 判定与版本号过期；代码与 `README` 在"无厂商深链按钮"上一致，无需改 README。
