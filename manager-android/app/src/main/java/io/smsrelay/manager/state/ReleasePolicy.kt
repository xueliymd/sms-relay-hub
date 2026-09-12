package io.smsrelay.manager.state

import io.smsrelay.manager.model.ReleaseEntry

object ReleasePolicy {
    fun latest(history: List<ReleaseEntry>): ReleaseEntry? = history.maxByOrNull { it.versionCode }

    fun updateAvailable(history: List<ReleaseEntry>, currentVersionCode: Int): Boolean =
        latest(history)?.versionCode?.let { it > currentVersionCode } ?: false
}
