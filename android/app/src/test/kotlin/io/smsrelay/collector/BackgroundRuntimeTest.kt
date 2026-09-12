package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class BackgroundRuntimeTest {
    @Test
    fun notificationPermissionIsRequiredOnlyOnAndroid13AndLater() {
        assertFalse(BackgroundRuntime.needsNotificationPermission(32))
        assertTrue(BackgroundRuntime.needsNotificationPermission(33))
    }

    @Test
    fun foregroundServiceUsesSpecialUseType() {
        // specialUse：常驻监听类服务的正确类型（对齐 GKD StatusService）。
        // dataSync 在 Android 15 有 6h/24h 预算，超时系统强停服务，不适合常驻监听。
        assertTrue(BackgroundRuntime.foregroundServiceType == "specialUse")
    }

    @Test
    fun immediateUploadHasDedicatedServiceAction() {
        assertTrue(BackgroundRuntime.ACTION_UPLOAD_NOW.endsWith("UPLOAD_NOW"))
    }
}
