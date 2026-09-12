# Relay 通用脚本 API 客户端

这个目录不是 Air724 专用适配器。`otp_client.py` 是普通 Python、服务器脚本、自动化任务和其他支持 WebSocket 的客户端可复用的参考实现。

## 实时取码

```python
from clients.script_api.otp_client import ScriptApiClient

client = ScriptApiClient(
    "http://127.0.0.1:8000",
    "后台创建的脚本 API 令牌",
    "+8613800138000",
)
otp = client.listen_once(timeout_seconds=60)
print(otp.code)
```

客户端会自动完成：

1. 将 `http://`/`https://` 平台地址转换成 `ws://`/`wss://`。
2. 连接 `/api/v1/ws/otp`。
3. 发送 Script API Token 完成鉴权。
4. 订阅指定接收手机号；`device_id`、`sender`、`keyword` 可选。脚本令牌不要求绑定设备 ID。
5. 只返回平台新推送的 OTP 事件，不返回短信正文。

认证成功后必须在 10 秒内发送一次手机号订阅；一条连接只能订阅一个手机号。参考客户端默认每 25 秒发送应用层 `{"type":"ping"}`，平台在 3 分钟没有任何客户端帧时自动断开连接。需要调整时可向 `listen_once` 传入 `ping_interval_seconds`。

## 断线补取

```python
otp = client.claim_latest(timeout_seconds=10)
if otp is not None:
    print(otp.code)
```

HTTP Claim 是断线恢复入口，不是正常轮询入口。请求必须带 `phone_number`；在线状态使用 WebSocket，重连后最多调用一次 Claim，然后继续等待 WebSocket。

## 鉴权区别

- Android、Root、iPhone 的设备凭据用于短信上报。
- Script API Token 用于 WebSocket 取码和 HTTP Claim。
- Script API Token 不填写到 Android、Root 或 iPhone 的设备凭据位置。
- Script API Token 不要求绑定设备；目标手机号是必填订阅范围，`device_id` 仅为可选来源筛选条件。

## 已验证链路

`backend/tests/test_script_api_network.py` 会启动真实 Uvicorn TCP 端口，创建设备和 Script API Token，再由独立 WebSocket 客户端等待推送。测试同时核对验证码、短信 `message_id`、Claim 结果和无可用记录时的 404 行为。
