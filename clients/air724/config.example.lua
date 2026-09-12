-- 将这些值复制到 main.lua 顶部 CONFIG 后再运行。
-- Air724U 使用旧版 Lua 5.1 接口；新版 LuatOS 硬件需要单独适配。
return {
    host = "192.168.1.10",
    port = 39300,
    tls = false,
    ws_path = "/api/v1/ws/otp",
    claim_path = "/api/v1/otp/claim",
    script_api_token = "在设备页添加合宙 724 取码设备后复制一次",
    phone_number = "+8613800138000",
    device_id = nil,
    sender = nil,
    keyword = nil,
    reconnect_max_seconds = 60,
    ping_seconds = 25,
}
