-- Relay Air724 MQTT SMS uploader
-- This is an upload-only companion to main.lua, which remains the OTP consumer.
PROJECT = "RELAY_AIR724_MQTT"
VERSION = "1.0.0"

require "log"
require "sys"
require "net"
require "socket"
require "mqtt"
require "sms"
require "json"
require "crypto"
require "misc"

local CONFIG = {
    host = "192.168.1.10",
    port = 1883,
    tls = false,
    mqtt_url = nil,
    username = "",
    password = "",
    keepalive = 300,
    device_id = "PASTE_AIR724_MQTT_DEVICE_ID_HERE",
    hmac_secret = "PASTE_BASE64URL_HMAC_SECRET_HERE",
    topic_prefix = "relay/v1/devices/",
    reconnect_max_seconds = 60,
}

local mqttc = nil
local queue = {}
local message_sequence = 0

local function log_info(message, ...)
    log.info("relay.air724.mqtt", message, ...)
end

local function json_escape(value)
    local text = tostring(value or "")
    return text:gsub("\\", "\\\\")
        :gsub('"', '\\"')
        :gsub("\r", "\\r")
        :gsub("\n", "\\n")
        :gsub("\t", "\\t")
        :gsub("%z", "\\u0000")
end

local function json_string(value)
    return '"' .. json_escape(value) .. '"'
end

local function base64url_encode(value)
    return crypto.base64_encode(value, #value)
        :gsub("%+", "-")
        :gsub("/", "_")
        :gsub("=+$", "")
end

local function base64url_decode(value)
    local alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    local encoded = tostring(value or ""):gsub("-", "+"):gsub("_", "/")
    while #encoded % 4 ~= 0 do encoded = encoded .. "=" end
    local output = {}
    for index = 1, #encoded, 4 do
        local a = alphabet:find(encoded:sub(index, index), 1, true)
        local b = alphabet:find(encoded:sub(index + 1, index + 1), 1, true)
        local c = alphabet:find(encoded:sub(index + 2, index + 2), 1, true)
        local d = alphabet:find(encoded:sub(index + 3, index + 3), 1, true)
        if not a or not b then return nil end
        a, b = a - 1, b - 1
        c = c and c - 1 or 0
        d = d and d - 1 or 0
        output[#output + 1] = string.char(a * 4 + math.floor(b / 16))
        if encoded:sub(index + 2, index + 2) ~= "=" then
            output[#output + 1] = string.char((b % 16) * 16 + math.floor(c / 4))
        end
        if encoded:sub(index + 3, index + 3) ~= "=" then
            output[#output + 1] = string.char((c % 4) * 64 + d)
        end
    end
    return table.concat(output)
end

local function nonce()
    message_sequence = message_sequence + 1
    return tostring(os.time()) .. "-" .. tostring(rtos.tick()) .. "-" .. tostring(message_sequence)
end

local function message_id(sender, content, received_at)
    message_sequence = message_sequence + 1
    local seed = table.concat({"air724-mqtt-v1", misc.getImei() or "", sender, received_at, content, tostring(message_sequence)}, "\n")
    return "air724-mqtt-v1:" .. crypto.sha256(seed, #seed)
end

local function device_now_iso()
    return os.date("!%Y-%m-%dT%H:%M:%SZ", os.time())
end

local function build_message(sender, content, received_at)
    local imei = misc.getImei() or ""
    local message_id_value = message_id(sender, content, received_at)
    return table.concat({
        "{",
        '"body":', json_string(content), ",",
        '"client_message_id":', json_string(message_id_value), ",",
        '"metadata":{"collector_version":"air724-mqtt-1","imei":', json_string(imei), "},",
        '"received_at":', json_string(received_at), ",",
        '"schema_version":1,',
        '"sender":', json_string(sender), ",",
        '"source":"air724_mqtt"',
        "}",
    })
end

local function build_envelope(raw_body)
    local timestamp = tostring(os.time())
    local message_nonce = nonce()
    local body_hash = crypto.sha256(raw_body, #raw_body)
    local canonical = timestamp .. "\n" .. message_nonce .. "\n" .. body_hash
    local secret = base64url_decode(CONFIG.hmac_secret)
    if not secret then return nil, "hmac_secret_is_invalid" end
    local signature = crypto.hmac_sha256(canonical, secret):fromHex()
    return json.encode({
        schema_version = "1",
        device_id = CONFIG.device_id,
        timestamp = timestamp,
        nonce = message_nonce,
        signature = base64url_encode(signature),
        payload_b64 = base64url_encode(raw_body),
    })
end

local function topic()
    return CONFIG.topic_prefix .. CONFIG.device_id .. "/messages"
end

local function connect_mqtt()
    local client_id = misc.getImei() or ("air724-" .. CONFIG.device_id)
    mqttc = mqtt.client(client_id, CONFIG.keepalive, CONFIG.username, CONFIG.password, 1)
    local target = CONFIG.mqtt_url
    local ok
    if target then
        local scheme = target:match("^(%a+):")
        ok = mqttc:connect(target, nil, scheme == "wss" and "wss" or "ws", nil, 30000)
    elseif CONFIG.tls then
        ok = mqttc:connect(CONFIG.host, CONFIG.port, "tcp_ssl", nil, 30000)
    else
        ok = mqttc:connect(CONFIG.host, CONFIG.port, "tcp", nil, 30000)
    end
    return ok
end

local function publish_pending()
    while #queue > 0 and mqttc do
        local item = queue[1]
        local envelope, reason = build_envelope(item.raw_body)
        if not envelope then
            table.remove(queue, 1)
            log_info("sms_rejected", reason)
        else
            local ok = mqttc:publish(topic(), envelope, 1, 0)
            if not ok then return false end
            table.remove(queue, 1)
            log_info("sms_forwarded")
        end
    end
    return true
end

local function enqueue_sms(sender, content)
    if type(sender) ~= "string" or type(content) ~= "string" then
        return
    end
    if content == "" then return end
    local raw_body = build_message(sender, content, device_now_iso())
    queue[#queue + 1] = {
        raw_body = raw_body,
    }
end

sms.setNewSmsCb(enqueue_sms)

sys.taskInit(function()
    local retry_seconds = 1
    while true do
        if not socket.isReady() then sys.waitUntil("IP_READY_IND", 300000) end
        if socket.isReady() then
            if connect_mqtt() then
                retry_seconds = 1
                if publish_pending() then
                    while mqttc do
                        local received, data = mqttc:receive(5000)
                        if not received and data ~= "timeout" then break end
                        if not publish_pending() then break end
                    end
                end
            end
            if mqttc then pcall(mqttc.disconnect, mqttc) end
            mqttc = nil
        end
        sys.wait(retry_seconds * 1000)
        retry_seconds = math.min(retry_seconds * 2, CONFIG.reconnect_max_seconds)
    end
end)

sys.init(0, 0)
sys.run()
