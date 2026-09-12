# Android 真机验收

每次发布记录 Android 版本、设备型号、SIM 数量、应用版本、服务端版本和测试时间。证据只保留状态码、队列计数和脱敏 ID 后缀。

- [ ] 首次安装拒绝权限时不会入队；授权后能接收新消息。
- [ ] 前台、后台、锁屏状态各收到一条授权测试消息，服务端各只生成一条记录。
- [ ] 双 SIM 设备记录可用的 slot 信息；厂商未提供 slot 时允许为空。
- [ ] 拒绝可选电话权限时短信仍可入队；授权时只上报系统实际提供的订阅 metadata。
- [ ] 过期 subscription ID + 有效 slot 时按 slot 找到 active subscription，不上报过期 ID。
- [ ] subscription ID 与 slot 冲突时，以 active subscription 记录的 slot、ID、carrier 为准。
- [ ] subscription ID 查询异常且 slot 查询仅返回空时保留 best-effort ID，但不尝试读取 phone number。
- [ ] 系统未提供 MSISDN 时，通过管理端 SIM profile UI 手工映射号码。
- [ ] 从已有 v1 安装升级到 v2 时，带待发送消息的 Room 队列完成迁移并继续上传。
- [ ] 断网后消息进入 Room；恢复网络后 WorkManager 自动上传。
- [ ] 进程被杀、设备重启后待处理队列仍存在并最终上传。
- [ ] 同一消息广播两次只产生一个 `client_message_id`。
- [ ] 轮换凭据后旧请求返回 `401`；保存新凭据后 `AUTH_BLOCKED` 队列恢复。
- [ ] 服务端 `429/5xx` 时指数退避，不出现快速循环。
- [ ] 上传成功、冲突或永久拒绝后 Room 中发送方/正文列为 `NULL`。
- [ ] Logcat、界面、截图和 smoke 输出不包含令牌、密钥、完整发送方、正文或访问码。
- [ ] 注入配置、Keystore、Room/DAO、scheduler 和 status 异常时，工作线程无未捕获 `Exception`，状态不含异常详情或短信/SIM 值。

只有 APK 构建、上述真机矩阵以及服务端记录对照都完成后，才可标记 Android 端到端验收通过。
