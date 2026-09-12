package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class AppReleaseTest {
    private val response = """
        {
          "update_available": true,
          "release": {
            "platform": "android",
            "channel": "stable",
            "version_code": 10,
            "version_name": "1.0.9",
            "published_at": "2026-09-01T08:00:00Z",
            "min_sdk": 26,
            "mandatory": false,
            "notes": "Safe updater",
            "size_bytes": 3000000,
            "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "signer_sha256": "f6bfe7a63a07950244cc0a8e21f551bdce9cd5bd375c22876de2d97eaaab27ff",
            "download_url": "/assets/releases/android/10/Relay-SMS-Forwarder-1.0.9.apk"
          }
        }
    """.trimIndent()

    @Test
    fun parsesAvailableRelease() {
        val check = AppReleaseJson.parseCheck(response)

        assertTrue(check.updateAvailable)
        assertEquals(10, check.release?.versionCode)
        assertEquals("1.0.9", check.release?.versionName)
    }

    @Test
    fun policyAcceptsOnlyStrictlyNewerCompatibleVersions() {
        val release = requireNotNull(AppReleaseJson.parseCheck(response).release)

        assertTrue(AppUpdatePolicy.canOffer(release, currentVersionCode = 9, sdkInt = 35))
        assertFalse(AppUpdatePolicy.canOffer(release, currentVersionCode = 10, sdkInt = 35))
        assertFalse(AppUpdatePolicy.canOffer(release.copy(minSdk = 36), currentVersionCode = 9, sdkInt = 35))
    }

    @Test
    fun resolvesOnlySameOriginReleaseDownloads() {
        assertEquals(
            "https://relay.example/assets/releases/android/10/update.apk",
            AppUpdatePolicy.resolveDownloadUrl(
                "https://relay.example",
                "/assets/releases/android/10/update.apk",
            ),
        )

        assertFailsWith<IllegalArgumentException> {
            AppUpdatePolicy.resolveDownloadUrl(
                "https://relay.example",
                "https://attacker.invalid/update.apk",
            )
        }
    }
}
