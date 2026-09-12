package io.smsrelay.collector

import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

class AppUpdateClient {
    fun check(baseUrl: String, currentVersionCode: Long, channel: String = "stable"): AppReleaseCheck {
        val endpoint = "${SmsRelayConfig.validateBaseUrl(baseUrl)}/api/v1/releases/android/latest" +
            "?channel=$channel&current_version_code=$currentVersionCode"
        val connection = URL(endpoint).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 5_000
            connection.readTimeout = 5_000
            connection.setRequestProperty("Accept", "application/json")
            require(connection.responseCode == 200) { "update check failed with HTTP ${connection.responseCode}" }
            val body = connection.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() }
            AppReleaseJson.parseCheck(body)
        } finally {
            connection.disconnect()
        }
    }

    fun download(baseUrl: String, release: AppRelease, directory: File): File {
        directory.mkdirs()
        require(directory.isDirectory) { "update directory is unavailable" }
        val finalFile = File(directory, "Relay-SMS-Forwarder-${release.versionCode}.apk")
        val temporaryFile = File(directory, "${finalFile.name}.part")
        temporaryFile.delete()
        val connection = URL(AppUpdatePolicy.resolveDownloadUrl(baseUrl, release.downloadUrl)).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 10_000
            connection.readTimeout = 30_000
            connection.instanceFollowRedirects = false
            require(connection.responseCode == 200) { "update download failed with HTTP ${connection.responseCode}" }
            val declaredLength = connection.contentLengthLong
            require(declaredLength < 0 || declaredLength == release.sizeBytes) { "update size mismatch" }
            val digest = MessageDigest.getInstance("SHA-256")
            var copied = 0L
            connection.inputStream.use { input ->
                FileOutputStream(temporaryFile).use { output ->
                    val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        copied += count
                        require(copied <= release.sizeBytes) { "update is larger than declared" }
                        digest.update(buffer, 0, count)
                        output.write(buffer, 0, count)
                    }
                    output.fd.sync()
                }
            }
            require(copied == release.sizeBytes) { "update size mismatch" }
            val actualHash = digest.digest().joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
            require(actualHash == release.sha256) { "update hash mismatch" }
            finalFile.delete()
            require(temporaryFile.renameTo(finalFile)) { "cannot finalize update download" }
            return finalFile
        } catch (error: Exception) {
            temporaryFile.delete()
            throw error
        } finally {
            connection.disconnect()
        }
    }
}
