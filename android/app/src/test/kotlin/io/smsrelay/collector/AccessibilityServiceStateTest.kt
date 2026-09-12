package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class AccessibilityServiceStateTest {
    @Test
    fun detectsConfiguredServiceInColonSeparatedSetting() {
        assertTrue(
            AccessibilityServiceState.containsService(
                enabledServices = "other.app/.Service:io.smsrelay.collector/.RelayKeepAliveAccessibilityService",
                packageName = "io.smsrelay.collector",
                serviceClassName = "io.smsrelay.collector.RelayKeepAliveAccessibilityService",
            ),
        )
    }

    @Test
    fun acceptsFullyQualifiedComponentNameIgnoringCase() {
        assertTrue(
            AccessibilityServiceState.containsService(
                enabledServices = "IO.SMSRELAY.COLLECTOR/io.smsrelay.collector.RelayKeepAliveAccessibilityService",
                packageName = "io.smsrelay.collector",
                serviceClassName = "io.smsrelay.collector.RelayKeepAliveAccessibilityService",
            ),
        )
    }

    @Test
    fun rejectsMissingOrDifferentService() {
        assertFalse(
            AccessibilityServiceState.containsService(
                enabledServices = null,
                packageName = "io.smsrelay.collector",
                serviceClassName = "io.smsrelay.collector.RelayKeepAliveAccessibilityService",
            ),
        )
        assertFalse(
            AccessibilityServiceState.containsService(
                enabledServices = "io.smsrelay.collector/.DifferentService",
                packageName = "io.smsrelay.collector",
                serviceClassName = "io.smsrelay.collector.RelayKeepAliveAccessibilityService",
            ),
        )
    }
}
