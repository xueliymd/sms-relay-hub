# Android 与 GitHub 参考项目对照

核对日期：2026-08-23。

参考项目：

- Android 应用：[pppscn/SmsForwarder](https://github.com/pppscn/SmsForwarder)，核对提交 `ce931a03739a6a2b0dfc83161bf812a94fd5e45e`。
- Root 模块：[zgcwkjOpenProject/Magisk_SmsForwarder](https://github.com/zgcwkjOpenProject/Magisk_SmsForwarder)，核对提交 `254575885f9e3b1cb4ba2b0b9e64d4bb90d9bd7b`，上游标签 `1.2`。

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
