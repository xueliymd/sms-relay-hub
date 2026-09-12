-- Relay 合宙 Air724 OTP consumer
-- Runtime: official openLuat/Luat_Lua_Air724U socket/http/json APIs.
-- Android/Root/iPhone remain HTTP ingest devices; this script only consumes OTPs.
PROJECT = "RELAY_AIR724"
VERSION = "1.0.8"

require "log"
require "sys"
require "net"
require "socket"
require "http"
require "json"
require "crypto"
require "common"

-- Edit only this block before downloading the script to the Air724 device.
local CONFIG = {
    host = "192.168.1.10",
    port = 8000,
    tls = false,
    ws_path = "/api/v1/ws/otp",
    claim_path = "/api/v1/otp/claim",
    script_api_token = "PASTE_SCRIPT_API_TOKEN_HERE",
    phone_number = "+8613800138000",
    device_id = nil,
    sender = nil,
    keyword = nil,
    reconnect_max_seconds = 60,
    ping_seconds = 25,
}

local seen_otp_ids = {}
local seen_otp_order = {}
local max_seen_otp_ids = 256
local last_otp_created_at = nil

local function log_info(message, ...)
    log.info("relay.air724", message, ...)
end

local function bxor(a, b)
    local result, bit_value = 0, 1
    while a > 0 or b > 0 do
        local left, right = a % 2, b % 2
        if left ~= right then result = result + bit_value end
        a = math.floor(a / 2)
        b = math.floor(b / 2)
        bit_value = bit_value * 2
    end
    return result
end

local function random_bytes(count)
    local bytes = {}
    for index = 1, count do bytes[index] = string.char(math.random(0, 255)) end
    return table.concat(bytes)
end

local function base64(value)
    return crypto.base64_encode(value, #value)
end

local function url_encode(value)
    return tostring(value):gsub("([^%w%-_%.~])", function(character)
        return string.format("%%%02X", string.byte(character))
    end)
end

local function relay_url(path)
    local scheme = CONFIG.tls and "https://" or "http://"
    return scheme .. CONFIG.host .. ":" .. tostring(CONFIG.port) .. path
end

local function socket_host_header()
    return CONFIG.host .. ":" .. tostring(CONFIG.port)
end

local function ws_client_frame(payload, opcode)
    local length = #payload
    local header = string.char(0x80 + (opcode or 1))
    if length < 126 then
        header = header .. string.char(0x80 + length)
    elseif length < 65536 then
        header = header .. string.char(0x80 + 126, math.floor(length / 256), length % 256)
    else
        error("Air724 client payload is too large")
    end
    local mask = random_bytes(4)
    local masked = {}
    for index = 1, length do
        masked[index] = string.char(bxor(string.byte(payload, index), string.byte(mask, ((index - 1) % 4) + 1)))
    end
    return header .. mask .. table.concat(masked)
end

local function ws_send_json(client, value)
    return client:send(ws_client_frame(json.encode(value), 1))
end

local function ws_handshake(client)
    local key = base64(random_bytes(16))
    local request = table.concat({
        "GET ", CONFIG.ws_path, " HTTP/1.1\r\n",
        "Host: ", socket_host_header(), "\r\n",
        "Upgrade: websocket\r\n",
        "Connection: Upgrade\r\n",
        "Sec-WebSocket-Key: ", key, "\r\n",
        "Sec-WebSocket-Version: 13\r\n",
        "\r\n",
    })
    if not client:send(request) then return false, "handshake_send_failed" end
    local response = ""
    for _ = 1, 10 do
        local ok, data = client:recv(3000, "RELAY_AIR724_WAKE")
        if ok then
            response = response .. data
            if response:find("\r\n\r\n", 1, true) then break end
        elseif data ~= "timeout" then
            return false, "handshake_receive_failed"
        end
    end
    if not response:match("HTTP/1%.1 101") then return false, "websocket_upgrade_rejected" end
    return true
end

local function parse_ws_frames(buffer)
    local frames = {}
    while #buffer >= 2 do
        local first = string.byte(buffer, 1)
        local second = string.byte(buffer, 2)
        local opcode = first % 16
        local masked = second >= 128
        local length = second % 128
        local offset = 3
        if length == 126 then
            if #buffer < 4 then break end
            length = string.byte(buffer, 3) * 256 + string.byte(buffer, 4)
            offset = 5
        elseif length == 127 then
            error("Air724 client does not support 64-bit WebSocket payloads")
        end
        local mask
        if masked then
            if #buffer < offset + 3 then break end
            mask = {string.byte(buffer, offset, offset + 3)}
            offset = offset + 4
        end
        if #buffer < offset + length - 1 then break end
        local payload = buffer:sub(offset, offset + length - 1)
        buffer = buffer:sub(offset + length)
        if masked then
            local decoded = {}
            for index = 1, length do
                decoded[index] = string.char(bxor(string.byte(payload, index), mask[((index - 1) % 4) + 1]))
            end
            payload = table.concat(decoded)
        end
        frames[#frames + 1] = {opcode = opcode, payload = payload}
    end
    return frames, buffer
end

local function decode_json(payload)
    local value, ok, error_info = json.decode(payload)
    if not ok or type(value) ~= "table" then
        log_info("invalid_server_json", error_info or "decode_failed")
        return nil
    end
    return value
end

local function on_otp(event)
    if not event.otp_id or seen_otp_ids[event.otp_id] then return end
    -- Claim recovery returns the source message creation time. Ignore a valid
    -- but older row that was already superseded while this client was offline.
    if event.created_at and last_otp_created_at and event.created_at <= last_otp_created_at then
        log_info("otp_older_than_watermark", event.otp_id, event.created_at)
        return
    end
    seen_otp_ids[event.otp_id] = true
    seen_otp_order[#seen_otp_order + 1] = event.otp_id
    if #seen_otp_order > max_seen_otp_ids then
        local expired_id = table.remove(seen_otp_order, 1)
        seen_otp_ids[expired_id] = nil
    end
    if event.created_at then last_otp_created_at = event.created_at end
    -- The consumer receives the exact code and IDs. The SMS body is intentionally not sent.
    log_info("otp_received", event.otp_id, event.message_id, event.code)
    sys.publish("RELAY_AIR724_OTP", event)
end

local function process_ws_frame(client, frame)
    if frame.opcode == 8 then return false, "closed" end
    if frame.opcode == 9 then
        client:send(ws_client_frame(frame.payload, 10))
        return true, nil
    end
    if frame.opcode ~= 1 then return true, nil end
    local event = decode_json(frame.payload)
    if event and event.type == "otp" then on_otp(event) end
    return true, event and event.type or nil
end

local function claim_json()
    local request = {phone_number = CONFIG.phone_number}
    if CONFIG.device_id then request.device_id = CONFIG.device_id end
    if CONFIG.sender then request.sender = CONFIG.sender end
    if CONFIG.keyword then request.keyword = CONFIG.keyword end
    local completed, response_code, response_body = false, nil, nil
    http.request(
        "POST",
        relay_url(CONFIG.claim_path),
        nil,
        { ["Authorization"] = "Bearer " .. CONFIG.script_api_token, ["Content-Type"] = "application/json" },
        json.encode(request),
        30000,
        function(result, status_code, _, body)
            completed, response_code, response_body = true, status_code, body
            log_info("claim_recovery", result, status_code)
        end
    )
    while not completed do sys.wait(100) end
    if tostring(response_code) ~= "200" or not response_body then return end
    local event = decode_json(response_body)
    if event then
        event.type = "otp"
        on_otp(event)
    end
end

-- Only this reconnect-recovery function calls HTTP Claim. Normal delivery is WebSocket-only.
local function recover_after_reconnect()
    claim_json()
end

local function run_websocket_session(client, is_reconnect)
    local ok, reason = ws_handshake(client)
    if not ok then return false, reason end
    if not ws_send_json(client, {type = "auth", token = CONFIG.script_api_token}) then return false, "auth_send_failed" end
    if not ws_send_json(client, {
        type = "subscribe",
        phone_number = CONFIG.phone_number,
        device_id = CONFIG.device_id,
        sender = CONFIG.sender,
        keyword = CONFIG.keyword,
    }) then return false, "subscribe_send_failed" end

    local buffer, seconds_since_ping = "", 0
    local subscribed, recovered = false, false
    while true do
        local received, data, wake_data = client:recv(1000, "RELAY_AIR724_WAKE")
        if received then
            buffer = buffer .. data
            local frames
            frames, buffer = parse_ws_frames(buffer)
            for _, frame in ipairs(frames) do
                local frame_ok, event_type = process_ws_frame(client, frame)
                if not frame_ok then return false, "server_closed" end
                if event_type == "subscribed" then subscribed = true end
            end
        elseif data == "RELAY_AIR724_WAKE" and wake_data then
            buffer = buffer .. wake_data
        elseif data ~= "timeout" then
            return false, "socket_receive_failed"
        end
        seconds_since_ping = seconds_since_ping + 1
        if seconds_since_ping >= CONFIG.ping_seconds then
            if not ws_send_json(client, {type = "ping"}) then return false, "ping_failed" end
            seconds_since_ping = 0
        end
        if is_reconnect and subscribed and not recovered then
            recover_after_reconnect()
            recovered = true
        end
    end
end

sys.taskInit(function()
    math.randomseed(os.time())
    local reconnect_seconds, is_reconnect = 1, false
    while true do
        if not socket.isReady() then sys.waitUntil("IP_READY_IND", 300000) end
        if socket.isReady() then
            local client = socket.tcp(CONFIG.tls)
            local connected = client and client:connect(CONFIG.host, CONFIG.port)
            if connected then
                local ok, reason = run_websocket_session(client, is_reconnect)
                log_info("session_end", ok, reason)
                client:close()
                is_reconnect = true
                reconnect_seconds = 1
            else
                if client then client:close() end
                log_info("connect_failed", reconnect_seconds)
                is_reconnect = true
                sys.wait(reconnect_seconds * 1000)
                reconnect_seconds = math.min(reconnect_seconds * 2, CONFIG.reconnect_max_seconds)
            end
        else
            sys.wait(20000)
        end
    end
end)

sys.init(0, 0)
sys.run()
