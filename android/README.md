# Android Collector

非 Root 原生 Kotlin 客户端。应用只监听本机新到达的 `SMS_RECEIVED` 广播，不申请 `READ_SMS`，不读取历史记录，也不修改系统消息。`SMS_DELIVER` 主要面向系统默认短信应用，本应用不依赖该通道。

## 数据流

1. Receiver 在广播回调入口记录 `received_at`，再根据发送方、设备消息时间和正文计算稳定 `client_message_id`。
2. 发送方与正文用 Android Keystore AES-GCM 加密后写入 Room 队列。
3. 新短信先走进程内即时上传路径；Room 加密队列负责断网、进程中断和失败重试，WorkManager 只承担可靠性兜底。每次请求使用新生成的 timestamp、nonce 和 HMAC 签名。
4. `2xx` 标记成功，`409` 标记幂等冲突，`401/403` 暂停等待凭据更新，`429/5xx/I/O` 使用指数退避重试。
5. 成功、冲突或永久拒绝后，本地队列立即清空发送方和正文密文，只保留时间、ID、SIM、状态和错误代号。应用状态中的“广播到响应”使用单调时钟统计广播回调入口到服务器提交并返回响应的总耗时。

队列容量按已完结记录控制，最多保留 500 条上传成功、幂等冲突或永久拒绝记录；超过容量时只清理最旧的已完结记录，不会挤掉仍在等待网络或凭据的短信。应用不会在日志或状态栏中显示设备密钥、完整发送方、正文或访问码。

## 构建

- JDK 17
- Android SDK Platform 35
- Gradle 8.9（CI 会安装；本目录未提交 Wrapper）
- 最低 API 26，目标 API 35

```powershell
gradle -p android clean testDebugUnitTest lintDebug assembleDebug --no-daemon
```

The source contract can run without a JDK or Android SDK:

```powershell
pwsh -NoProfile -File android/tests/subscription_snapshot_contract.ps1
```

当前工作机若没有 JDK/Gradle，只能做源码和契约检查，不能把 APK 构建标记为通过。GitHub Actions 配置位于 `.github/workflows/android.yml`。

## 注册与配置

1. 管理端创建 `client_type=android_app`、`auth_kind=hmac` 的设备。
2. 推荐在设备操作栏点击“生成扫码配置”，二维码有效 5 分钟且只能使用一次；二维码不包含长期 HMAC 密钥。
3. 安装 Debug/私有签名 APK，打开应用并点击“扫描后台二维码自动配置”，首次使用允许相机权限。采集端会通过 HTTPS/HTTP 单次兑换配置，并使用 Android Keystore 保存密钥。
4. 也可以继续手工填写服务器 HTTP/HTTPS 地址、设备 ID 和 HMAC 密钥；设备 ID 与 `credential.secret` 只显示一次，不要写入 Git、聊天、截图或日志。
5. 保存后密钥输入框会清空。点击“授予短信权限”，允许短信权限；Android 13 及以上再允许后台通知。
5. 应用使用前台服务保持运行，通知栏会显示“Relay短信转发”；切到后台后仍会继续接收新短信并上传。
6. 在系统设置中允许应用自启动、后台活动，并将电池策略设为“不限制”；需要时把应用锁定在最近任务中。小米/Redmi、华为/Honor、OPPO/OnePlus/realme、vivo、Samsung 等品牌的菜单名称可能不同。
7. 凭据轮换后重新保存配置；应用会把 `AUTH_BLOCKED` 队列恢复为待同步并重新调度 WorkManager。

## 后台运行与开机恢复

- 应用配置保存后会启动前台服务，服务返回 `START_STICKY`，系统重建进程后会继续启动。
- 手机重启或应用升级后，已保存配置会由开机接收器恢复前台服务和上传任务。
- Android 13 及更高版本必须允许后台通知，否则前台服务通知无法正常显示。
- 电池优化、自启动、后台活动和“不受限制”属于系统设置，请按一次性告示手动开启。应用内的按钮只做**跳转**：电池优化按钮打开系统“电池优化”列表页（由你手动把本应用设为“不优化/不受限制”），后台限制按钮打开“应用电池用量”页，自启动按钮只打开应用详情页（不硬拼厂商组件，避免黑屏）；真正的白名单/自启动开关仍需你在系统里确认。
- 通知栏磁贴是开/关开关：点击暂停或恢复转发，长按打开应用（由 manifest 的 `QS_TILE_PREFERENCES` 承接）。暂停后短信仍写入本地队列但不再外发，恢复后自动补传。
- Android 13/14 侧载安装时，系统可能对无障碍服务显示“受限制的设置”。点击应用内的无障碍按钮，先打开“应用信息”，进入右上角“⋮”并选择“允许受限制的设置”，再返回无障碍设置手动开启；应用不能绕过这项系统安全限制。
- 前台服务降低切后台后被系统回收的概率，但不能绕过厂商的强制清理策略；完成上述设置后再做后台短信验证。

## Optional SIM metadata

`READ_PHONE_STATE` and `READ_PHONE_NUMBERS` are requested separately from `RECEIVE_SMS`.
Rejecting these optional permissions does not affect SMS capture or queueing. When Android
provides the values, the collector includes the slot, subscription ID, carrier name, and
phone number in the relay payload. Missing values are omitted.

When phone-state access is available, active subscription records are authoritative. A
stale subscription hint is discarded, and the receiver falls back to a valid slot hint.
If the subscription hint resolves successfully but conflicts with the slot hint, the
resolved subscription record supplies the slot, subscription ID, and carrier.

If the subscription ID lookup fails and the slot lookup merely returns no record, the
collector keeps the broadcast subscription ID as an unverified best-effort hint. It does
not use that unverified ID for a phone-number lookup. The hint is removed only when the
subscription ID and every available fallback explicitly report no active record.

Android and the mobile carrier may omit the MSISDN even when optional permissions are
granted. In that case, an administrator can manually map the phone number to the device
and slot through the SIM profile UI.

Non-fatal receiver pipeline failures are contained inside the `goAsync` worker. The app
records only a constant failure status; exception messages and SMS/SIM values are never
placed in status text or logs. Fatal VM errors are not swallowed.

## 协议

请求头为 `X-Device-Id`、`X-Timestamp`、`X-Nonce`、`X-Signature`、`X-Idempotency-Key`。签名内容为：

```text
base64url(HMAC-SHA256(secret, timestamp + "\n" + nonce + "\n" + sha256(raw_json_body)))
```

`X-Idempotency-Key` 必须与 JSON 的 `client_message_id` 相同。服务端只接受 `android_app + hmac + source=android_app` 的绑定组合。

## 验收

协议脚本：

```powershell
$env:SMS_RELAY_BASE_URL='http://服务器IP:端口'
$env:SMS_RELAY_DEVICE_ID='<local-device-id>'
$env:SMS_RELAY_HMAC_SECRET='<local-secret>'
pwsh -NoProfile -File tests/android_ingest_e2e.ps1
```

脚本应输出 `first=201` 和 `second=200 duplicate=true`，不会打印环境变量或消息正文。真机矩阵见 `android/docs/real-device-validation.md`。
