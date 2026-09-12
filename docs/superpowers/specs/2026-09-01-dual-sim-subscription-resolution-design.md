# 双卡短信 Subscription 识别修复设计

## 目标

确保 Android 双卡设备收到 SIM2 短信时，客户端上传真实的 `subscription_id`、卡槽和本机号码，不因厂商广播字段缺失、错误 slot hint 或陈旧缓存显示成 SIM1。

## 根因

- 当前接收器只读取 Intent extras，没有从 `SmsMessage` 隐藏的 subscription 方法回退。
- payload 优先使用原始 slot hint，可能覆盖由有效 `subscription_id` 解析出的权威卡槽。
- slot-only 快照会被进程级缓存；换卡或厂商暂时返回错误映射后可能长期复用。

## 设计

1. 新增纯 Kotlin `SmsSubscriptionHintResolver`，统一解析官方字段、厂商字段、数字/字符串/数组值，并接收 `SmsMessage` 回退得到的 subscription ID。
2. 接收器在 extras 无有效 subscription ID 时，依次尝试 `getSubId` 和 `getSubscriptionId` 反射。
3. `SubscriptionSnapshotProvider` 继续以 subscription ID 为第一权威来源。
4. payload 使用解析后 snapshot 的 slot；仅在 snapshot 没有 slot 时回退原始广播 slot。
5. 仅缓存同时具有 slot 和 subscription ID 的明确映射；模糊映射每条短信重新查询。
6. 不记录短信正文、完整号码或设备密钥；失败仍按原有常量状态处理。

## 验收

- 只有 message subId 的 SIM2 短信解析到对应 subscription。
- 错误 slot hint 与正确 subscription 冲突时，正确 subscription 的 slot 获胜。
- slot-only 查询不会被缓存。
- 完整 slot+subscription 查询仍可缓存并可显式清空。
- Android 单元测试、lint、debug APK 构建通过。
- 雷电模拟器可安装、启动并完成基本权限/服务检查；模拟器不具备真实双 SIM 基带时，以单元测试覆盖双卡分支。
