package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertEquals

class AppSigningCertificateSelectorTest {
    @Test
    fun singleSignerArchiveFallsBackToSigningHistoryOnAndroidNine() {
        assertEquals(
            setOf("current"),
            AppSigningCertificateSelector.current(
                hasMultipleSigners = false,
                apkContentsSigners = emptyList(),
                signingCertificateHistory = listOf("old", "current"),
                legacySigners = emptyList(),
            ),
        )
    }

    @Test
    fun installedSingleSignerAcceptsItsCertificateHistory() {
        assertEquals(
            setOf("old", "current"),
            AppSigningCertificateSelector.authorized(
                hasMultipleSigners = false,
                apkContentsSigners = listOf("current"),
                signingCertificateHistory = listOf("old", "current"),
                legacySigners = emptyList(),
            ),
        )
    }

    @Test
    fun legacySignaturesRemainACompatibilityFallback() {
        assertEquals(
            setOf("legacy"),
            AppSigningCertificateSelector.current(
                hasMultipleSigners = false,
                apkContentsSigners = emptyList(),
                signingCertificateHistory = emptyList(),
                legacySigners = listOf("legacy"),
            ),
        )
    }
}
