package io.smsrelay.collector

import org.json.JSONObject
import java.net.URL
import java.time.Instant
import java.util.UUID

data class EnrollmentQrPayload(
    val baseUrl: String,
    val deviceId: String,
    val token: String,
    val expiresAt: Instant,
) {
    companion object {
        private const val TYPE = "sms-relay-enroll"

        fun parse(raw: String, now: Instant = Instant.now()): EnrollmentQrPayload {
            val json = runCatching { JSONObject(raw.trim()) }
                .getOrElse { throw IllegalArgumentException("二维码内容不是有效配置") }
            require(json.optInt("v", -1) == 1) { "二维码版本不支持" }
            require(json.optString("type") == TYPE) { "这不是 Relay 设备配置二维码" }
            val baseUrl = SmsRelayConfig.validateBaseUrl(json.optString("url"))
            val deviceId = json.optString("device_id").trim()
            runCatching { UUID.fromString(deviceId) }
                .getOrElse { throw IllegalArgumentException("二维码中的设备 ID 无效") }
            val token = json.optString("token").trim()
            require(token.length in 32..256) { "二维码授权已损坏" }
            val expiresAt = runCatching { Instant.parse(json.optString("expires_at")) }
                .getOrElse { throw IllegalArgumentException("二维码有效期无效") }
            require(expiresAt.isAfter(now)) { "二维码已过期，请在后台重新生成" }
            return EnrollmentQrPayload(baseUrl, deviceId, token, expiresAt)
        }
    }
}

data class EnrollmentResponse(val config: SmsRelayConfig, val deviceName: String?)

class EnrollmentClient {
    fun consume(payload: EnrollmentQrPayload): EnrollmentResponse {
        val endpoint = URL("${payload.baseUrl}/api/v1/enrollment/consume")
        val connection = endpoint.openConnection() as java.net.HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 8_000
            connection.readTimeout = 8_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            val body = JSONObject()
                .put("device_id", payload.deviceId)
                .put("enrollment_token", payload.token)
                .toString()
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val responseBody = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (status !in 200..299) {
                throw IllegalStateException(
                    when (status) {
                        410 -> "二维码已过期，请在后台重新生成"
                        400 -> "二维码无效或已被使用"
                        else -> "扫码配置失败（HTTP $status）"
                    },
                )
            }
            val json = JSONObject(responseBody)
            require(json.optString("device_id") == payload.deviceId) { "服务器返回的设备不匹配" }
            require(json.optString("client_type") == "android_app") { "二维码不是采集端配置" }
            require(json.optString("auth_kind") == "hmac") { "二维码鉴权类型不支持" }
            val secret = json.optString("hmac_secret").trim()
            val config = SmsRelayConfig(payload.baseUrl, payload.deviceId, secret)
            EnrollmentResponse(config, json.optString("device_name").takeIf { it.isNotBlank() })
        } finally {
            connection.disconnect()
        }
    }
}
