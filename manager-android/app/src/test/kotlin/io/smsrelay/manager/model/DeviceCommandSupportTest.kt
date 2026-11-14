package io.smsrelay.manager.model

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class DeviceCommandSupportTest {
    @Test
    fun allowsOnlyClientsWithPersistentCommandPollers() {
        assertTrue(supportsDeviceCommands("android_app"))
        assertTrue(supportsDeviceCommands("root_module"))
        assertFalse(supportsDeviceCommands("ios_shortcut"))
        assertFalse(supportsDeviceCommands("ve_tweak"))
        assertFalse(supportsDeviceCommands("air724"))
        assertFalse(supportsDeviceCommands("air724_mqtt"))
        assertFalse(supportsDeviceCommands("unknown"))
    }
}
