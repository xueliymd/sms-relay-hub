package io.smsrelay.manager.state

import io.smsrelay.manager.model.ReleaseEntry
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ReleasePolicyTest {
    @Test
    fun selectsHighestVersionAndOnlyReportsStrictlyNewerManagerBuilds() {
        val history = listOf(release(1, "1.0.0"), release(3, "1.2.0"), release(2, "1.1.0"))

        assertEquals(3, ReleasePolicy.latest(history)?.versionCode)
        assertTrue(ReleasePolicy.updateAvailable(history, 2))
        assertFalse(ReleasePolicy.updateAvailable(history, 3))
    }

    private fun release(versionCode: Int, versionName: String) = ReleaseEntry(
        platform = "manager",
        channel = "stable",
        versionCode = versionCode,
        versionName = versionName,
        publishedAt = "2026-09-01T10:30:00Z",
        notes = "manager",
        sizeBytes = 1,
        sha256 = "hash",
        downloadUrl = "/manager.apk",
    )
}
