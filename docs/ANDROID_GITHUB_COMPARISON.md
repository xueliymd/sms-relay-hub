# Android 与 GitHub 参考项目对照

核对日期：2026-11-14。

参考项目：

- Android 应用：[pppscn/SmsForwarder](https://github.com/pppscn/SmsForwarder)，最新核对提交 `a142f9270624e96b15ecfae53035581354fb6434`（2026-11-01）；上次基线为 `ce931a03739a6a2b0dfc83161bf812a94fd5e45e`。
- Root 模块：[zgcwkjOpenProject/Magisk_SmsForwarder](https://github.com/zgcwkjOpenProject/Magisk_SmsForwarder)，最新核对提交仍为 `254575885f9e3b1cb4ba2b0b9e64d4bb90d9bd7b`，上游标签 `1.2`，无新提交。

## 2026-11-14 上游增量复核

`pppscn/SmsForwarder` 从上次基线到最新 `main` 共前进 5 个提交：

| 上游提交 | 上游变更 | 对本项目的判断 |
| --- | --- | --- |
| `a3d2302` | 模板标签正则把 `([^=]+)` 改为 `(.+?)`，允许 `(?=...)` 等含 `=` 的正则。 | 本项目没有同款模板 DSL，不照搬；若以后增加消息模板，需要复用“正则段允许 `=`”的回归样本。 |
| `0466e1d` | 服务端默认 fail-closed，修复 CVE-2026-110618。 | 安全方向应吸收。本项目已有设备类型/鉴权/来源三方绑定、默认关闭远程指令授权、HMAC 防重放和持久 nonce，不需要复制其内置 HTTP Server。 |
| `fd9852c` | 修复 CI APK 输出路径。 | 仅上游流水线问题；本项目保持现有 Android 构建产物路径约定。 |
| `45cb86b` | 按定位精度显式选择 provider，避免 `getBestProvider` 与用户设置不一致。 | 本项目不采集定位，不引入定位权限和 provider 逻辑。 |
| `a142f92` | Android 16/17 部分 ROM 的 `divideMessage()` 抛 `getGroupIdLevel1` 异常时，降级到 GSM 7bit 160/153、UCS2 70/67 本地切分。 | 这是**发送短信**路径，当前接收端不受影响。以后实现远程 `send_sms` 时必须吸收该降级，并补设备/卡槽/重复发送测试。 |

补充结论：

- 当前 Android 接收端已经使用 `messages.joinToString("")` 合并多段 PDU，但缺少专门的“多段长短信合并”纯逻辑回归测试；不影响本次 iPhone 集成，列入后续测试债。
- `SmsForwarder v3.5.0` 的多通道能力（Bark 随机 IV/重要警告、Telegram Topic/MarkdownV2、MQTT qos/retained、大文本通知、邮件别名、WOL、联系人姓名/归属地标签等）主要服务其独立客户端转发模型。本项目是集中式服务端规则引擎，不建议整体搬运。
- Root 模块上游无更新，因此此前关于事件 watcher、spool、HMAC、幂等和低频扫描兜底的结论继续有效。

以下 08-23 和 09-12 的架构对照保留为历史结论；其中“上游 HEAD 仍是 `ce931a0…`”只代表当时状态，已被本次 5 个提交更新。

## Android 应用

| 项目 | GitHub 参考项目 | Relay短信转发 1.0.8 | 结论 |
| --- | --- | --- | --- |
| 短信入口 | `SMS_RECEIVED`、默认短信应用使用的 `SMS_DELIVER`、WAP 和部分厂商动作 | `SMS_RECEIVED` | 普通转发应用的可靠入口是 `SMS_RECEIVED`；不伪装默认短信应用，也不无条件加入厂商私有广播。 |
| 广播后处理 | 主要提交 WorkManager | `goAsync` 后立即进入内存直传，Room 队列持久化兜底 | 当前方案首传链路更短；WorkManager 只负责失败重试。 |
| 防重复 | 任务层处理 | 发送方、短信原始时间和正文生成稳定 `client_message_id`，本地唯一键和服务端幂等共同去重 | 即使 ROM 重复广播，也不会生成两条不同业务消息。 |
| 后台运行 | 前台服务、`START_STICKY`、WorkManager，可选双进程、一像素、静音音频等保活 | 单前台服务、`START_STICKY`、开机/升级恢复、常驻通知 | 保留低功耗单服务方案；不复制高耗电保活组合。 |
| 厂商限制 | 提示开启通知、自启动、关联启动、后台运行和取消电池限制 | 教程提供同类一次性设置说明 | 厂商强杀属于系统策略，应用不能靠隐藏接口可靠绕过。 |
| SIM 信息 | 读取电话和订阅信息 | 可选读取活动订阅快照，缺号码时允许后台手动绑定 | 拒绝可选电话权限不影响短信正文接收。 |
| 安全与可靠性 | 多种转发目标 | 单一自托管平台、HMAC、防重放、正文加密队列、失败重试 | 当前范围更窄，但认证、幂等和本地敏感数据边界更明确。 |

`received_at` 在 `SMS_RECEIVED` 广播回调入口采集；PDU 的短信时间只用于稳定消息 ID。状态中的“广播到响应”使用单调时钟，表示广播入口到平台提交事务并返回 HTTP 响应的耗时，不使用短信通知弹窗时间。

## Root 模块

| 项目 | GitHub 参考项目 | Relay Root 0.3.9 | 结论 |
| --- | --- | --- | --- |
| 新短信发现 | 每 10 秒 `content query` 轮询 | 只读 Provider/SQLite 水位线，监听 `mmssms.db`、`sms.db` 及 WAL/SHM 变化，低频扫描兜底 | 当前方案延迟更低。 |
| 上传失败 | 上游脚本可能仍推进 LAST_ID | 私有 spool、inflight claim、失败重试和恢复 | 当前方案不会因一次网络失败丢失短信。 |
| 鉴权 | 通用转发配置 | 设备 UUID + HMAC，请求防重放与幂等键 | UUID 负责定位设备，HMAC 负责证明请求来自该设备，不能二选一。 |
| 上报通道 | HTTP | HTTP 默认，WebSocket/MQTT 可选并可回退 HTTP | MQTT 只使用后端可消费的标准设备主题，WS 帧和主题有大小边界。 |
| 隐私 | 上游脚本可能记录完整正文日志 | 状态和日志不写正文、手机号、验证码或密钥 | 保持当前实现。 |
| 双卡 | 依赖 Provider 输出 | `sub_id`、SIM Provider、活动订阅和手动绑定多级解析 | 当前路由信息更完整。 |

因此 Root 模块不回退到上游的固定 10 秒轮询。0.3.9 保留事件 watcher、spool、HMAC、幂等和退避重试，并增加 HTTP/WS/MQTT 选择、失败回退和传输边界校验。

---

## 远程"发短信"参考实现核对（2026-09-12）

核对对象同 08-23 那次：`pppscn/SmsForwarder`，`main` HEAD 仍是 `ce931a03739a6a2b0dfc83161bf812a94fd5e45e`
（提交日期 2026-07-27），因此 08-23 的结论无需重做。

### 上游实现路径与体量

| 文件 | 体量 | 内容 |
| --- | --- | --- |
| `app/src/main/kotlin/cn/ppps/forwarder/server/controller/SmsController.kt` | 2246 B（blob `92b99ce2…`） | `POST /sms/send` + `POST /sms/query`，发送侧有效逻辑约 19 行 |
| `app/src/main/kotlin/cn/ppps/forwarder/utils/PhoneUtils.kt` | `sendSms(subId, mobileList, message)` **62 行** | 真正的发送实现 |

上游 `sendSms` 的实质：`SEND_SMS` 权限检查 → 从 `App.SimInfoList[simSlot-1].mSubscriptionId` 取卡 →
`SmsManager.getSmsManagerForSubscriptionId(subId)` → 正文 ≥70 字用 `divideMessage` + `sendMultipartTextMessage`，
否则 `sendTextMessage` → 配 `PendingIntent`。

### 可以直接借鉴的部分

- **分段发送**：`divideMessage` + `sendMultipartTextMessage`（长短信必做）。
- **指定卡槽**：`getSmsManagerForSubscriptionId(subId)`。
- 我们 `minSdk 26`，**不需要**上游那段 `Build.VERSION_CODES.LOLLIPOP_MR1` 以下的 `mSubId` 反射 hack。

### 不要照抄的三个点

1. **`App.SimInfoList[simSlotIndex]` 直接下标取卡**：`SimInfoList` 为空或 `simSlot` 越界会抛
   `IndexOutOfBoundsException`。上游代码里只有一句 `//TODO：取不到卡槽信息时，采用默认卡槽发送` 的注释，**并未实现**。
   我们应改为：按 `sim_slot` 查 `device_sim_profiles.subscription_id`，取不到明确回传 `sim_slot_unavailable`，**不猜默认卡**。
2. **兜底 `SmsManager.getDefault()`**：多卡设备上走系统默认卡，**可能发错卡**。我们必须强制指定 subId。
3. **号码列表语义**：上游支持 `;`/`，`/`,` 分隔的多个号码（`replace("；",";")` 后再 split）。
   我们的目标号码是单个，不需要这一层；照搬反而引入歧义。

### 参考代码给不了的（工时真正所在）

上游是**设备内置 HTTP Server + 控制方直连**：参数在请求体里、**用完即弃，没有服务端、没有队列**。
我们是**平台排队 + 设备轮询拉取**，于是：

1. **指令必须持久化参数**，而 `device_commands`（迁移 0010）只有 `command_type` / `status` / `result_*` / 时间戳，
   **没有参数列** → 需要新迁移。且号码在本项目是最敏感数据（现有代码一律 `nonce` + `ciphertext` + `key_version`
   密文，配 `lookup_hash` 与 `sender_summary` 摘要），参数列**不能是明文 `TEXT`**。
2. **轮询形态有"取指令→执行→回传"窗口**：`DeviceCommandPoller.kt:201-226` 只有同进程重入锁，
   进程在该窗口内被杀，重启后会**再取一次并再执行一遍**。只读指令危害有限，但**发短信是写操作 → 会重复发送**。
   上游是同步 HTTP，调用方自己知道成败，根本不存在这个问题。
   → 这正是审计报告 **M4**，它已被标注为"上线发短信等写指令前必须补"，属**前置依赖**而非 send_sms 自带的活。
3. **端侧安全姿态不同**：我们是**每类指令独立授权开关、默认全关** + 需要二次确认 + 结果密文回传；
   上游是配置级的 API 总开关。
4. **客户端面数不同**：上游只有一个 App；我们要动 网页 + 采集端 Android + 管理端 App + Root 模块。
5. 本项目**双卡这块反而比上游更有底**：`device_sim_profiles.subscription_id` 已在采集（见
   `2026-09-01-dual-sim-subscription-resolution-design.md`），服务端可以把 `sim_slot` 一路带到指令参数里，
   不需要上游那种"取不到就默认卡槽"的兜底。

### 修正后的估算

**单做 `send_sms` ≈ 1 天量级**（此前笼统沿用 v2 的 4~6 天、单模块 2~3 天，偏保守，应下调）：

| 部分 | 可交付性 |
| --- | --- |
| 后端：迁移（参数列，加密）+ 参数通道 + `DEVICE_COMMAND_TYPES` 加 `send_sms` + 接口 + 审计 | 可在本机完成并跑测试验证 |
| 后端：指令原子 claim / 端侧去重（M4 前置） | 同上 |
| 网页：远程控制页加"发送短信"表单 + 二次确认 | 可在本机完成并跑 tsc/vitest 验证 |
| 采集端：权限引导 + 授权开关 + 执行分支（≈60 行可借鉴上游）+ 二次确认 | 代码可写，**本机无 JDK/Android SDK/gradle，编译验证不了** |
| 管理端 App / Root 模块：白名单与 UI | 同上，需在 Android 环境编译 |
