package io.smsrelay.collector

import java.net.URL
import java.security.MessageDigest
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

object SmsRelaySigner {
    fun sign(secret: String, timestamp: String, nonce: String, body: String): String {
        val unpaddedSecret = secret.trim().trimEnd('=')
        val key = try {
            Base64.getUrlDecoder().decode(
                unpaddedSecret + "=".repeat((4 - unpaddedSecret.length % 4) % 4),
            )
        } catch (error: IllegalArgumentException) {
            throw IllegalArgumentException("HMAC secret is not valid base64url", error)
        }
        require(key.isNotEmpty()) { "HMAC secret is empty" }
        val digest = sha256Hex(body.toByteArray(Charsets.UTF_8))
        val canonical = "$timestamp\n$nonce\n$digest".toByteArray(Charsets.UTF_8)
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        return Base64.getUrlEncoder().withoutPadding().encodeToString(mac.doFinal(canonical))
    }

    private fun sha256Hex(value: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(value)
        .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
}

data class SmsRelayConfig(val baseUrl: String, val deviceId: String, val hmacSecret: String) {
    init {
        validateBaseUrl(baseUrl)
        require(deviceId.isNotBlank()) { "device ID is required" }
        SmsRelaySigner.sign(hmacSecret, "0", "config-check", "{}")
    }

    companion object {
        fun validateBaseUrl(value: String): String {
            val normalized = value.trim().removeSuffix("/")
            val url = runCatching { URL(normalized) }.getOrNull()
            require(url != null && (url.protocol.equals("http", ignoreCase = true) || url.protocol.equals("https", ignoreCase = true)) && !url.host.isNullOrBlank() && url.userInfo == null && url.query == null && url.ref == null) {
                "base URL must be an HTTP(S) origin"
            }
            return normalized
        }
    }
}
