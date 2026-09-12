# iPhone 快捷指令

推荐使用简化入口：

```text
POST <RELAY_BASE_URL>/api/v1/ingest/shortcut
```

iPhone 只需要 Bearer Token、自动化传入的整条信息和当前日期。服务端负责拆分发件人和正文、校验设备时间、生成稳定消息 ID，并复用现有入库和验证码识别逻辑。本机或局域网 IP 可使用 HTTP，公网生产环境建议使用 HTTPS。

完整手机配置见 `shortcut-definition.md`，请求边界见 `request-contract.md`，验收清单见 `VALIDATION.md`。快捷指令最多对网络或 5xx 临时错误重试一次，服务端会按相同消息幂等去重。

旧版完整协议 `/api/v1/ingest/messages` 保持兼容，但不再推荐在手机上手工配置其全部字段。

## 安全

- 每台 iPhone 使用独立的 `ios_shortcut + bearer` 设备凭据。
- Token 只放在 `Authorization` 请求头。
- 不要分享包含真实 Token 的快捷指令。
- Token 出现在截图或聊天记录后，应立即轮换。
