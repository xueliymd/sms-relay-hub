package io.smsrelay.collector

import org.json.JSONObject
import java.net.URI

data class AppRelease(
    val channel: String,
    val versionCode: Long,
    val versionName: String,
    val publishedAt: String,
    val minSdk: Int,
    val mandatory: Boolean,
    val notes: String,
    val sizeBytes: Long,
    val sha256: String,
    val signerSha256: String,
    val downloadUrl: String,
)

data class AppReleaseCheck(
    val updateAvailable: Boolean,
    val release: AppRelease?,
)

object AppReleaseJson {
    fun parseCheck(raw: String): AppReleaseCheck {
        val root = JSONObject(raw)
        val release = root.optJSONObject("release")?.let(::parseRelease)
        return AppReleaseCheck(
            updateAvailable = root.optBoolean("update_available", false),
            release = release,
        )
    }

    fun parseRelease(raw: String): AppRelease = parseRelease(JSONObject(raw))

    fun toJson(release: AppRelease): String = JSONObject()
        .put("platform", "android")
        .put("channel", release.channel)
        .put("version_code", release.versionCode)
        .put("version_name", release.versionName)
        .put("published_at", release.publishedAt)
        .put("min_sdk", release.minSdk)
        .put("mandatory", release.mandatory)
        .put("notes", release.notes)
        .put("size_bytes", release.sizeBytes)
        .put("sha256", release.sha256)
        .put("signer_sha256", release.signerSha256)
        .put("download_url", release.downloadUrl)
        .toString()

    private fun parseRelease(value: JSONObject): AppRelease {
        require(value.getString("platform") == "android") { "unsupported release platform" }
        return AppRelease(
            channel = value.getString("channel"),
            versionCode = value.getLong("version_code"),
            versionName = value.getString("version_name"),
            publishedAt = value.getString("published_at"),
            minSdk = value.getInt("min_sdk"),
            mandatory = value.getBoolean("mandatory"),
            notes = value.getString("notes"),
            sizeBytes = value.getLong("size_bytes"),
            sha256 = AppUpdatePolicy.normalizeSha256(value.getString("sha256")),
            signerSha256 = AppUpdatePolicy.normalizeSha256(value.getString("signer_sha256")),
            downloadUrl = value.getString("download_url"),
        ).also {
            require(it.channel.matches(Regex("[a-z0-9][a-z0-9_-]{0,31}"))) { "invalid release channel" }
            require(it.versionCode > 0) { "invalid release version" }
            require(it.versionName.isNotBlank()) { "invalid release name" }
            require(it.minSdk in 21..100) { "invalid minimum SDK" }
            require(it.sizeBytes in 1..MAX_APK_SIZE_BYTES) { "invalid release size" }
            require(it.notes.length <= 4000) { "release notes are too long" }
        }
    }

    private const val MAX_APK_SIZE_BYTES = 500L * 1024L * 1024L
}

object AppUpdatePolicy {
    fun canOffer(release: AppRelease, currentVersionCode: Long, sdkInt: Int): Boolean =
        release.versionCode > currentVersionCode && release.minSdk <= sdkInt

    fun resolveDownloadUrl(baseUrl: String, downloadUrl: String): String {
        require(downloadUrl.startsWith("/assets/")) { "release download must be a local asset" }
        val base = URI(SmsRelayConfig.validateBaseUrl(baseUrl))
        val resolved = base.resolve(downloadUrl)
        require(resolved.scheme.equals(base.scheme, ignoreCase = true)) { "release download origin mismatch" }
        require(resolved.host.equals(base.host, ignoreCase = true)) { "release download origin mismatch" }
        require(effectivePort(resolved) == effectivePort(base)) { "release download origin mismatch" }
        require(resolved.userInfo == null && resolved.query == null && resolved.fragment == null) { "invalid release download URL" }
        return resolved.toString()
    }

    fun normalizeSha256(value: String): String {
        val normalized = value.trim().lowercase().replace(":", "")
        require(normalized.matches(Regex("[0-9a-f]{64}"))) { "invalid SHA-256" }
        return normalized
    }

    private fun effectivePort(uri: URI): Int = when {
        uri.port >= 0 -> uri.port
        uri.scheme.equals("https", ignoreCase = true) -> 443
        else -> 80
    }
}
