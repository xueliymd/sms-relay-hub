package io.smsrelay.collector

import android.content.Context
import java.io.File

class AppUpdateStore(context: Context) {
    private val appContext = context.applicationContext
    private val preferences = appContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    fun availableRelease(): AppRelease? = preferences.getString(KEY_RELEASE, null)
        ?.let { runCatching { AppReleaseJson.parseRelease(it) }.getOrNull() }

    fun saveAvailableRelease(release: AppRelease) {
        preferences.edit().putString(KEY_RELEASE, AppReleaseJson.toJson(release)).apply()
    }

    fun clearAvailableRelease() {
        downloadedFile()?.delete()
        preferences.edit().remove(KEY_RELEASE).remove(KEY_DOWNLOADED_PATH).apply()
    }

    fun saveDownloadedFile(file: File) {
        preferences.edit().putString(KEY_DOWNLOADED_PATH, file.absolutePath).apply()
    }

    fun downloadedFile(): File? = preferences.getString(KEY_DOWNLOADED_PATH, null)
        ?.let(::File)
        ?.takeIf { it.isFile }

    fun clearDownloadedFile() {
        downloadedFile()?.delete()
        preferences.edit().remove(KEY_DOWNLOADED_PATH).apply()
    }

    fun recordStatus(status: String) {
        preferences.edit().putString(KEY_STATUS, status.take(300)).apply()
    }

    fun status(): String? = preferences.getString(KEY_STATUS, null)

    private companion object {
        const val PREFERENCES = "app_update_state"
        const val KEY_RELEASE = "available_release"
        const val KEY_DOWNLOADED_PATH = "downloaded_path"
        const val KEY_STATUS = "status"
    }
}
