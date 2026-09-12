package io.smsrelay.collector

import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

class SmsRelayHttpClient {
    fun post(config: SmsRelayConfig, payload: SmsRelayPayload): UploadResponse {
        val body = payload.toJson()
        val timestamp = (System.currentTimeMillis() / 1000L).toString()
        val nonce = UUID.randomUUID().toString()
        val connection = (URL("${config.baseUrl.trimEnd('/')}/api/v1/ingest/messages").openConnection() as HttpURLConnection)
        return try {
            connection.requestMethod = "POST"
            // WorkManager owns retries; each attempt stays bounded and never logs request material.
            connection.connectTimeout = 3_000
            connection.readTimeout = 3_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("X-Device-Id", config.deviceId)
            connection.setRequestProperty("X-Timestamp", timestamp)
            connection.setRequestProperty("X-Nonce", nonce)
            connection.setRequestProperty("X-Signature", SmsRelaySigner.sign(config.hmacSecret, timestamp, nonce, body))
            val startedAt = System.nanoTime()
            connection.setRequestProperty("X-Idempotency-Key", payload.idempotencyKey)
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            val statusCode = connection.responseCode
            UploadResponse(statusCode, UploadTiming.elapsedMillis(startedAt, System.nanoTime()))
        } finally {
            connection.disconnect()
        }
    }
}

data class UploadResponse(val statusCode: Int, val elapsedMillis: Long)
