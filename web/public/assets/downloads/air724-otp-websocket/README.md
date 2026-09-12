# Relay Air724 OTP 客户端

这个客户端用于合宙 Air724/Air724U 设备连接 Relay 短信转发平台，接收平台推送的最新短信验证码。

## 兼容运行时

- 当前脚本使用合宙官方 `openLuat/Luat_Lua_Air724U` 旧版 Lua 5.1 运行时接口。
- 依赖 `socket.tcp`、`http.request`、`json` 和 `crypto`，不使用新版 LuatOS 的 `websocket` 模块。
- 新版 LuatOS 硬件不直接运行本文件；后续适配新版硬件时只替换客户端适配层，服务端 WebSocket 协议不变。

## 配置

编辑 `main.lua` 顶部的 `CONFIG`：

- `host`：Relay 服务器地址或 IP，不填协议头。
- `port`：Relay 服务器端口；HTTP/WS 测试环境通常为 `8000`，TLS/WSS 环境填对应端口。
- `tls`：使用 HTTPS/WSS 时设为 `true`，局域网 HTTP/WS 测试设为 `false`。
- `script_api_token`：在后台“设备”页添加“合宙 724 取码”设备后显示一次的脚本 API 令牌。
- `phone_number`：取码手机号，必须与平台中短信接收号码一致。
- `device_id`、`sender`、`keyword`：可选筛选条件，留空表示不增加该条件。

## 工作方式

1. 客户端通过 WebSocket 发送 `auth` 和 `subscribe`。
2. 平台收到新短信并提取验证码后，立即向匹配的 WebSocket 连接推送 `otp` 事件。
3. 客户端按 `otp_id` 去重，并通过系统消息事件 `RELAY_AIR724_OTP` 提供验证码。
4. WebSocket 断线重连并完成订阅后，仅执行一次 HTTP Claim 作为漏发恢复；正常取码不轮询 HTTP。
5. WebSocket 保持 ping，网络失败使用递增重连等待，避免高频空转。

这个脚本是取码端，不是短信上报端。Android 应用、Android Root 和 iPhone 快捷指令仍使用原有 HTTP 上报接口。
