# Relay Air724 客户端

本目录提供两种互相独立的 Air724/Air724U 脚本：`main.lua` 用 WebSocket 接收平台推送的验证码，`mqtt_ingest.lua` 用 MQTT 把模块收到的完整短信上报到平台。不要把两个脚本配置成同一个设备类型或把两个用途混在一个脚本里。

## MQTT 短信上报

`mqtt_ingest.lua` 对应后台设备类型“合宙 724 MQTT 短信上报”，使用 HMAC 设备凭据。它只发布 `relay/v1/devices/<device_id>/messages`，不订阅取码主题；MQTT 桥接服务再把消息转入平台的 `/api/v1/ingest/mqtt`。

配置 `mqtt_ingest.lua` 顶部的服务器地址、端口、TLS、设备 UUID 和一次性 HMAC 密钥。脚本在设备侧生成 UTC `received_at`、稳定 `client_message_id` 和每次发布的 nonce；离线期间短信留在内存队列，连接恢复后按顺序发送。模块真实运行、运营商短信回调和外部 MQTT Broker 仍需在 Air724 硬件上验收。

服务器端 MQTT 桥接是可选的，不启用时现有 Android、iPhone、Root 和 Air724 WebSocket 链路不变。部署时在 `deploy/.env` 填写外部 Broker 与 `RELAY_MQTT_BRIDGE_TOKEN`，再使用 Compose 的 `mqtt` profile 启动桥接服务。

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
- `device_id`、`sender`、`keyword`：可选筛选条件，留空表示不增加该条件。`device_id` 不是认证参数，脚本取码认证只使用 `script_api_token`。

## 工作方式

1. 客户端通过 WebSocket 发送 `auth` 和 `subscribe`。
2. 平台收到新短信并提取验证码后，立即向匹配的 WebSocket 连接推送 `otp` 事件。
3. 客户端按 `otp_id` 去重，并通过系统消息事件 `RELAY_AIR724_OTP` 提供验证码。
4. WebSocket 断线重连并完成订阅后，仅执行一次 HTTP Claim 作为漏发恢复；正常取码不轮询 HTTP。
5. WebSocket 默认每 25 秒发送应用层 `ping`；服务端认证和订阅阶段各有 10 秒超时，订阅后 3 分钟没有客户端帧会断开。网络失败使用递增重连等待，避免高频空转。

这个脚本是取码端，不是短信上报端。Android 应用、Android Root 和 iPhone 快捷指令仍使用原有 HTTP 上报接口。
