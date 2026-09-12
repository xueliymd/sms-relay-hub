# 会话承诺 vs 仓库代码 对账报告（采集端 / 管理端 Android App）

> 只读分析，未修改任何文件。
> 数据来源：`.ztext.txt`（54MB 会话全文）、`.workbuddy/memory/2026-09-12.md`、仓库 `docs/*`、仓库 Android 源码与 gradle。
> 仓库实际路径：`C:\Users\12071\Documents\Codex\2026-08-14\new-chat\work\sms-relay-hub-phase1\`（本工作目录仅含会话抽取与临时产物）。

## 关键前提（影响所有"已实现"判定）
生产事故前服务器最高运行到迁移 `0008`；本轮回合的 **转发规则引擎（迁移 `0009`）与远程控制 v1（迁移 `0010`）部署尝试失败并导致服务中断**（详见 R4 / `FULL_AUDIT_REPORT_2_2026-09-12.md:7-32`）。因此：
- 这两次更新**从未在生产成功可用**，相关功能在生产环境实际不可用（采集端 1.0.19/1.0.20 已引用这些端点，后端未上线时指令轮询恒为空）；
- 但二者**代码均已写入仓库并可编译通过**，故下表"实际状态"区分"代码已实现"与"生产可用"。

---

## 一、采集端（collector，1.0.16 → 1.0.20 / versionCode 21）逐条对账

| # | 项目 | 承诺 / 声称内容 | 出处 | 实际状态 | 证据 |
|---|---|---|---|---|---|
| C1 | 采集端 | 前台服务类型 `dataSync` → `specialUse`（避免 Android15 6h/24h 预算强停） | 记忆:30；ztext 多处 | **已实现** | `BackgroundRuntime.kt:14`、`SmsRelayForegroundService.kt:29`、`AndroidManifest.xml:40` |
| C2 | 采集端 | `WRITE_SECURE_SETTINGS` 声明 + 无障碍自愈（`AccessibilityAutoToggle`，需 ADB 授权） | 记忆:30-31 | **已实现** | `AndroidManifest.xml:20-22`、`AccessibilityAutoToggle.kt`、`KeepAliveWorker.kt:23-31` |
| C3 | 采集端 | 通知栏快捷磁贴 `RelayTileService`（"复活甲"） | 记忆:15 | **已实现** | `AndroidManifest.xml:50-59`、`RelayTileService.kt` |
| C4 | 采集端 | 电池优化 / 厂商自启动快捷按钮 | 记忆:12,15 | **已实现** | `MainActivity.kt:150-159`（`openBatteryOptimizationSettings`/`openAutoStartSettings`） |
| C5 | 采集端 | 无障碍 `onUnbind` 返回 `true`（被杀后系统自动重连） | 记忆:11 | **已实现** | `RelayKeepAliveAccessibilityService.kt:38` |
| C6 | 采集端 | **用户要求删除心跳闹钟**（保留无障碍复活 + 15 分钟 WorkManager 看门狗） | 记忆:41；ztext:37250（精简节） | **已实现（代码）** | `KeepAliveWatchdog.kt` 仅 15min WM；全代码 Grep `HEARTBEAT/armHeartbeat/ACTION_HEARTBEAT/SCHEDULE_EXACT_ALARM` 无匹配；manifest 无 `SCHEDULE_EXACT_ALARM`；`canScheduleExactAlarms` 无残留 |
| C7 | 采集端 | 一键重启无障碍改后台线程（防 UI 冻结） | 记忆:47 | **已实现** | `MainActivity.kt:124-131`（`Thread{...restart()}`） |
| C8 | 采集端 | 保存配置时若已持 WSS 自动开启无障碍（`a11y_wanted` 标记防强制） | 记忆:31 | **已实现** | `MainActivity.kt:241`、`KeepAliveWorker.kt:24-31` |
| C9 | 采集端 | 远程控制 v1 采集端（1.0.19）：指令轮询/执行器、READ_SMS、三指令授权**默认全关**、30s 节流、重入保护、号码脱敏、查最近短信去 `LIMIT` 语法 | 记忆:59；DEFERRED_FEATURES.md:83 | **代码已实现并编译**（但生产缺 0010，见 R2） | `DeviceCommandClient.kt:198`（30s）、`:205`（原子重入）、`:176-180`（脱敏 `takeLast(4)`）、`:186`（`RECENT_SMS_LIMIT=5`）；`CommandSettingsStore.kt:15`（默认 false）；`AndroidManifest.xml:8`（READ_SMS） |
| C10 | 采集端 | 版本 1.0.16 → 1.0.20 | 记忆:84 | **已实现** | `android/app/build.gradle.kts:30-31`（versionCode 21 / 1.0.20） |

## 二、管理端（manager，1.0.5 → 1.0.8 / versionCode 9）逐条对账

| # | 项目 | 承诺 / 声称内容 | 出处 | 实际状态 | 证据 |
|---|---|---|---|---|---|
| M1 | 管理端 | 设备写操作：创建/轮换/撤销/恢复/永久删除 | 记忆:42；DEFERRED:17 | **已实现** | `RelayAdminClient.kt:147-178` |
| M2 | 管理端 | SIM 手机号映射修改/删除（**不预填脱敏号**，防掩码误提交） | 记忆:42；DEFERRED:20 | **已实现** | `MainActivity.kt:688-708`（hint 不预填注释:689）；`RelayAdminClient.kt:181-191` |
| M3 | 管理端 | 消息 已读未读 / 单删 / 批删 / 保留清理（retention） | 记忆:42 | **已实现** | `RelayAdminClient.kt:205,217`（deleteMessage/runRetention）；`MainActivity.kt:732,757` |
| M4 | 管理端 | 管理员账号修改（改后强制重登） | 记忆:42 | **已实现 —— 但与设计文档冲突** | `RelayAdminClient.kt:222-232`；`FULL_AUDIT_REPORT_2_2026-09-12.md:60-65`（设计文档"非目标"第 2 条明确禁止，**需用户决策**） |
| M5 | 管理端 | 脚本 API 令牌创建 / 列表复制（撤销未做，后端无端点） | 记忆:42 | **已实现（撤销确未做）** | `RelayAdminClient.kt:235-249`；全文件无 `revokeApiClient` |
| M6 | 管理端 | 接口页"短信上报"地址修正为 `/api/v1/ingest/messages` + 设备指令轮询地址 | 记忆:80 | **已实现** | `ApiScreen.kt:21`（`/ingest/messages`）、`:24`（`commands/pending`） |
| M7 | 管理端 | 批量删除确认文案改准确（只删已加载条目） | 记忆:48 | **已实现** | `MainActivity.kt:748`（"将删除当前列表已加载的全部 ${loaded.size} 条"） |
| M8 | 管理端 | 版本 1.0.5 → 1.0.8 | 记忆:84 | **已实现** | `manager-android/app/build.gradle.kts:25-26`（versionCode 9 / 1.0.8） |

## 三、用户明确要求"删除 / 不要"的东西 —— 代码是否真没了（任务 #3）

| 项目 | 决定 | 出处 | 代码现状 | 结论 |
|---|---|---|---|---|
| 无忧行短信采集（通知监听源） | 用户 2026-09-10 决定不做 | DEFERRED_FEATURES.md:5 | 全仓库 Grep `无忧行/wuyouxing/NotificationListener` 仅命中 3 处文档，**0 处代码** | **干净删除，无半删** |
| 心跳闹钟 | 用户要求删除 | 记忆:41 | 见 C6：无残留引用（仅 `KeepAliveWorker.kt:9` 注释残留"续登记心跳闹钟"，见 R3） | **已删除** |
| 后台定时检查 / 管理端重登·卡券 / RCS / 设备端本地过滤 / 定时转发·导入导出 / 历史短信页 | 用户明确不做 | PENDING_FEATURES_REPORT:85-93 | 代码均无对应实现 | **一致未做** |

## 四、被记入"待办/未做"的项 —— 是否其实已做完（任务 #4）

经代码逐一核对，**全部待办项确实未实现，没有出现"做了却还列在待办"的情况**：

| 待办项 | 代码核对结果 | 结论 |
|---|---|---|
| 转发渠道扩展（Telegram/SMTP/Server酱/PushPlus/短信转发） | `forwarding.py:19` 仅 `wecom/dingtalk/feishu/bark/webhook` 5 渠道 | 确未做 |
| HMS 厂商推送 | `backend/app/services/push.py` 不存在（仅文档提及） | 确未做 |
| 远程控制 v2（send_sms / 查通话 / 查话簿） | 代码无 `SEND_SMS` / `READ_CALL_LOG` / `READ_CONTACTS` 引用 | 确未做 |
| 管理端 App 远程控制 UI | manager 无 DeviceCommand 下发入口（`ApiScreen.kt:24` 仅是参考 URL 文本） | 确未做 |
| 指令结果主动推送 | 无对应实现 | 确未做 |
| 真机/现场验证清单（7 项） | 纯现场任务，本地不可验证 | 确未做 |

## 五、声称已修 / 已实现但有回归或没修干净（任务 #5，最关键在于此）

| # | 项 | 实际状态 | 证据 |
|---|---|---|---|
| R1 | **管理员账号修改**：设计文档"非目标"明令禁止，但 1.0.7 已实现 | **与描述不符（矛盾）**，待用户决策是否回滚 | `FULL_AUDIT_REPORT_2:60-65`（引用设计文档"管理端 App 不执行…管理员账号修改"）；`RelayAdminClient.kt:222` |
| R2 | **远程控制 v1 / 转发规则引擎**：代码已实现并编译，但 `0009`/`0010` 部署失败、生产从未成功上线 → 这两个功能在生产**实际不可用**；采集端 1.0.19/1.0.20 已引用这些端点，后端未上线时指令轮询恒为空 | **声称已实现（代码）≠ 生产可用** | `FULL_AUDIT_REPORT_2:7-32`（用户实证迁移事故）；`DeviceCommandClient.kt:27`（端点依赖 0010） |
| R3 | `KeepAliveWorker.kt:9` 注释仍写"续登记心跳闹钟"，但心跳闹钟已删除 | **残留注释（轻微不符）**，逻辑已正确 | `KeepAliveWorker.kt:8-9` vs 无 `armHeartbeat`/`AlarmManager`（Grep 全仓仅注释命中） |
| R4 | **部署更新事故**：`apply-update.sh` 回滚只回源码、不回滚数据库迁移；新增迁移 `0010` 首次触发该历史缺陷，导致生产更新失败、`deploy-app` 容器 `Restarting(255)`，服务至今未恢复 | **声称已部署/已修复实则为线上事故**；后续已加 `pg_restore` 回滚（`test_deploy.py`），但生产尚未恢复 | `FULL_AUDIT_REPORT_2:18-23,84`；记忆:93-152 |
| R5 | 采集端所有保活/远程控制增强**仅编译 + 静态审查验证，无任何真机保活到达率 / 锁屏 15min 复活验证** | **未验证（高不确定性）** | DEFERRED_FEATURES.md:132-169（真机清单全部待做）；记忆:18 明确"无真机验证" |

---

## 最值得警惕的 5 条

1. **远程控制 v1 与转发规则"声称已实现"但生产从未成功上线（0009/0010 部署失败）**——文档标 ✅ 会严重误导；采集端已依赖这些端点，上线前必须先确认后端 0010 落地，否则指令功能静默失效。
2. **管理员账号修改违反设计文档"非目标"**——代码已做，与设计矛盾，须尽快拍板（更新设计文档承认扩围，或从 App 移除），否则属未授权范围扩张。
3. **生产服务因迁移回滚缺陷至今中断**——`apply-update.sh` 不回滚数据库迁移，新代码强退后旧代码不认 0010 即崩；虽已加 `pg_restore` 修复，但生产仍未恢复，属实际线上事故。
4. **全部采集端保活/远程控制改动零真机验证**——`specialUse`、无障碍自愈、磁贴、WSS 自动开启等均只过编译与静态审查，无任何锁屏复活/到达率实测，真机有效性未知。
5. **部署包混入 `web/tsconfig.tsbuildinfo`（含本机绝对路径）**且 `DEPLOYMENT_AUDIT_2026-08-23.md` 仍写容器监听 8000（实为 39200）——部署红线与文档过期，存在信息泄露与误导风险。
