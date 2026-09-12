package io.smsrelay.manager.net

import java.net.URI

class BaseUrl private constructor(
    val value: String,
    val secure: Boolean,
) {
    fun http(path: String): String {
        require(path.startsWith('/')) { "endpoint path must start with /" }
        return value + path
    }

    fun webSocket(path: String): String {
        val scheme = if (secure) "wss" else "ws"
        return scheme + ":" + value.substringAfter(':') + path.also {
            require(it.startsWith('/')) { "endpoint path must start with /" }
        }
    }

    companion object {
        fun parse(raw: String): BaseUrl {
            val candidate = raw.trim()
            require(candidate.isNotEmpty()) { "platform address is required" }
            val uri = runCatching { URI(candidate) }
                .getOrElse { throw IllegalArgumentException("invalid platform address", it) }
            val scheme = uri.scheme?.lowercase()
            require(scheme == "http" || scheme == "https") { "only HTTP and HTTPS are supported" }
            require(uri.host != null) { "platform host is required" }
            require(uri.userInfo == null) { "credentials are not allowed in the platform address" }
            require(uri.rawQuery == null && uri.rawFragment == null) { "query and fragment are not allowed" }
            require(uri.rawPath.isNullOrEmpty() || uri.rawPath == "/") { "platform address must not include a path" }
            val normalized = URI(scheme, null, uri.host.lowercase(), uri.port, null, null, null).toASCIIString()
            return BaseUrl(normalized, scheme == "https")
        }
    }
}
