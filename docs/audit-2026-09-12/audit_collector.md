# SMS 中转采集端（io.smsrelay.collector）源码审查报告

只读静态审查，未修改任何文件。版本锁定：versionName `1.0.20` / versionCode `21`（`android/app/build.gradle.kts:30-31`，与声明一致）。
所有 `dataSync`、`heartbeat`、`setComponent`、`SCHEDULE_EXACT_ALARM` 的命中**均只在注释里**，代码实际已改用 `specialUse` + WorkManager，未见残留。

> 说明：`DeviceCommandClient`、`DeviceCommandExecutor`、`DeviceCommandPoller` 三个类当前同处一个文件 `DeviceCommandClient.kt`（审查环境最初按 `DeviceCommandExecutor.kt` 列出，但磁盘真实文件名为 `DeviceCommandClient.kt`，无重复类定义、可编译）。下文引用以 `DeviceCommandClient.kt` 为准。

---

## 一、确认已正确实现

1. **前台服务类型 specialUse 三处一致** — `AndroidManifest.xml:40` 声明 `foregroundServiceType="specialUse"` + `PROPERTY_SPECIAL_USE_FGS_SUBTYPE`；`SmsRelayForegroundService.kt:22-30` 在 SDK≥34 用 `FOREGROUND_SERVICE_TYPE_SPECIAL_USE`，低版本走无类型 `startForeground`；`BackgroundRuntime.kt:14` 常量也是 `"specialUse"`。三处吻合，且已声明 `FOREGROUND_SERVICE_SPECIAL_USE` 权限（`AndroidManifest.xml:14`）。
2. **无障碍服务 `onUnbind` 返回 true** — `RelayKeepAliveAccessibilityService.kt:38` 确实 `return true`。
3. **WRITE_SECURE_SETTINGS + 程序化开启/重启无障碍** — `AndroidManifest.xml:19-22` 已声明并带 `tools:ignore="ProtectedPermissions"`；`AccessibilityAutoToggle.kt` 用 `canWriteSecureSettings` 守门并写 `ENABLED_ACCESSIBILITY_SERVICES`/`ACCESSIBILITY_ENABLED`，`enable()`/`restart()` 逻辑完整。
4. **磁贴服务注册完整** — `AndroidManifest.xml:50-59`：`exported="true"` + `BIND_QUICK_SETTINGS_TILE` 权限 + `QS_TILE` intent-filter + `@drawable/ic_relay_tile`（资源 `res/drawable/ic_relay_tile.xml` 存在）。`RelayTileService.kt` 的 `onClick/onStartListening/onTileAdded` 均接线。
5. **自启动引导走保守方案** — `BackgroundRuntime.openAutoStartSettings`（`BackgroundRuntime.kt:91-97`）只打开 `ACTION_APPLICATION_DETAILS_SETTINGS`，全仓无任何 `setComponent` 厂商 Activity 调用（仅注释提到会黑屏）。
6. **心跳闹钟已彻底移除** — `KeepAliveWatchdog.kt` 现仅含 `enqueuePeriodicWorker`（15 分钟 WorkManager）。全仓无 `armHeartbeat`/`Heartbeat` 残留引用，Manifest 无 `SCHEDULE_EXACT_ALARM`，`MainActivity` 无精确闹钟按钮。
7. **远程控制轮询节流** — `DeviceCommandClient.kt:198` `MIN_POLL_INTERVAL_MS=30_000` + `lastPollAt` 节流成立。
8. **签名约定** — `DeviceCommandClient.request`（`DeviceCommandClient.kt:51-78`）：GET pending 用空 body 签名（`sign(..., "")`），POST result 用 JSON body 签名，与 `SmsRelaySigner.sign`（`SmsRelaySigner.kt:10-25` 的 `timestamp\nnonce\nsha256(body)`）对齐。
9. **短信查询 provider 兼容** — `DeviceCommandClient.kt:142-158`：`sortOrder` 仅 `${Telephony.Sms.DATE} DESC`（无 LIMIT），游标内 `while (... && items.size < RECENT_SMS_LIMIT)` 截断。
10. **trigger_upload 真的触发上报** — `DeviceCommandClient.kt:166-173` 的 `triggerUpload` 调用 `SmsRelayUploadRunner.trigger(context)`，非空转。
11. **重入保护防递归** — `DeviceCommandPoller.poll` 用 `AtomicBoolean polling` + `lastPollAt` 双保险，避免 trigger→poll→trigger 递归（`DeviceCommandClient.kt:195-207`）。
12. **六层保活均实际接线**（非死代码）：
    - 前台服务：`BackgroundRuntime.start` ← `RelayApplication`/`BootReceiver`/`MainActivity`/`SmsRelayForegroundService.onTaskRemoved`
    - 无障碍复活：`RelayKeepAliveAccessibilityService.onServiceConnected` + `onUnbind=true`
    - 双悬浮窗：前台服务挂 `APP_WINDOW_TYPE`、无障碍服务挂 `accessibilityWindowType()`（`KeepAliveOverlay.kt`）
    - 开机自启：`BootReceiver`（BOOT_COMPLETED + MY_PACKAGE_REPLACED）
    - WorkManager 看门狗：`KeepAliveWatchdog.enqueuePeriodicWorker` 被 5 处调用
    - 磁贴复活：`RelayTileService`
13. **授权默认全关** — `CommandSettingsStore` 默认 `false`（`CommandSettingsStore.kt:15`），三类指令 key 映射齐全且有测试背书（`CommandSettingsStoreTest.kt`）。

---

## 二、发现的问题

### 中 (Medium)
- **M1 — 协程线程误用（KeepAliveWorker 阻塞 WorkManager 调度线程）**
  `KeepAliveWorker.kt:30` 在 `suspend doWork()` 内调用 `AccessibilityAutoToggle.restart(context)`；而 `AccessibilityAutoToggle.restart` 内部有 `Thread.sleep(REBIND_DELAY_MS)`（`AccessibilityAutoToggle.kt:74`，常量 1000ms）。
  这是一段 `Thread.sleep` 跑在 WorkManager 的协程/Executor 线程上，违反协程最佳实践，会把该 Worker 线程阻塞 1 秒（期间若其他周期任务复用该线程会被拖住）。应改用 `kotlinx.coroutines.delay`。`MainActivity` 的按钮路径虽用了 `Thread{}` 包裹（`MainActivity.kt:125`），但 KeepAlive 自愈路径没有。
  **为什么是问题**：审计明确点名“协程/线程是否正确”，此处属于未彻底修掉的协程阻塞；且 `restart` 被 `runCatching` 包住，即使静默失败也容易让人误以为自愈已发生。

### 低 (Low)
- **L1 — 心跳移除后残留过期注释（与“已移除”声明矛盾）**
  `KeepAliveWorker.kt:9` 注释仍写“若转发配置仍在则**续登记心跳闹钟**并重新拉起前台服务”。心跳已在本次改造中删除，此处描述已不实，属“删了一半注释”的回归迹象，易误导后续维护。
- **L2 — `onUnbind` 返回 true 对 AccessibilityService 的实际自愈效果有限**
  `RelayKeepAliveAccessibilityService.kt:38` 返回 true。对普通 `Service`，`onUnbind=true` 会在客户端重连时触发 `onRebind`；但无障碍服务由系统按 `ENABLED_ACCESSIBILITY_SERVICES` 设置绑定，**系统不会因 `onUnbind` 返回 true 就自动重新拉起**——真正的“被杀后复活”依赖系统重绑 + 本 App 的 WorkManager 看门狗/前台服务拉起。代码仍正确（看门狗链路兜底），但“onUnbind 自动重连”这一说法被高估，建议不要单靠它。
- **L3 — 死代码：`ImmediateUploadPayloadStore.take()`**
  `ImmediateUploadPayloadStore.kt:20-27` 的 `take(clientMessageId)` 全仓无任何调用方（仅 `offer`/`poll`/`clear` 被使用）。属“类存在但从未调用”的无用代码。
- **L4 — `MainActivity.saveConfig` 在 UI 线程执行 Binder 写入**
  `MainActivity.kt:243-250` 直接在主线程调用 `AccessibilityAutoToggle.enable(this)`，而 `enable()` 内部会 `Settings.Secure.putString/putInt`（Binder IPC）。虽非网络阻塞，但与同文件按钮路径（用 `Thread{}` 包裹 `restart`/`enable`，`MainActivity.kt:122-147`）做法不一致，属 UI 线程副作用，建议统一放到后台线程。
- **L5 — `runImmediate` 失败时对内存队列做整体清空**
  `SmsRelayUploadRunner.kt:85,91` 在 `AuthBlocked`/`Retryable`/`PermanentFailure` 分支调用 `ImmediateUploadPayloadStore.clear()`（清空**全部**内存项，而非仅失败的那个）。依赖 DB 侧 `runOnce` 兜底重传，故不丢短信；但若同批有多条、仅首条失败，其余内存项会被丢弃、改由 WorkManager 重传，行为略显粗暴。低危，仅作提示。

---

## 三、无法确认 / 需运行时/设备验证的疑点

1. **`onUnbind=true` 在真机各 ROM 上的行为**：理论上无障碍服务的系统重绑不取决于 `onUnbind` 返回值，但缺乏真机验证；建议用“关闭无障碍→进程被杀→系统是否自动回连”做端到端回归。
2. **`AccessibilityAutoToggle.enable/restart` 的实际效果**：依赖已 ADB 授予 `WRITE_SECURE_SETTINGS`。写入 `ENABLED_ACCESSIBILITY_SERVICES` 的格式为 `包名/全限定类名`（`AccessibilityAutoToggle.kt:48,68`），与 `AccessibilityServiceState.containsService` 的 fullComponent 匹配（`AccessibilityServiceState.kt:9-15`），逻辑自洽；但部分厂商 ROM 对 programmatic enable 有额外拦截，需在目标机型验证。
3. **WorkManager 15 分钟看门狗在极端省电策略下是否被合并/延迟**：属厂商调度行为，静态无法判断。
4. **`query_recent_sms` 在个别定制 ROM 上 `Telephony.Sms.Inbox.CONTENT_URI` 的列名/排序兼容**：代码已去掉 LIMIT，但 `ADDRESS/BODY/DATE` 列名差异无法静态覆盖，建议多机型验证（特别是去除 `READ_SMS` 授权时回退说明路径已覆盖）。

---

## 结论摘要（面向修复）

- **大多数列出的改造真实成立且接线正确**：specialUse 三处一致、无障碍 onUnbind=true、WRITE_SECURE_SETTINGS 声明、磁贴注册、保守自启动引导、心跳彻底移除无悬空、远程控制节流/签名/provider 兼容/trigger_upload/重入保护均落地，六层保活全部接线，版本号一致。
- **真正需要修的点很少且偏轻**：最重要是 **M1（KeepAliveWorker 内 `Thread.sleep` 阻塞协程线程，应改 `delay`）**；其余为过期注释（L1）、对 onUnbind 自愈能力的误读（L2）、死代码（L3）、UI 线程 Binder 写入（L4）、内存队列整体清空（L5）。
- **未发现“声称已修但实际没修/功能空转”的高危回归**；最贴近“没彻底修干净”的是 M1 的协程阻塞与 L1 的过期注释——它们正好对应审计最关心的“协程线程正确性”。
