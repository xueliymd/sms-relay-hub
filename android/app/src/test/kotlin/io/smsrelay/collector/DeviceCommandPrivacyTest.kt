package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertEquals

class DeviceCommandPrivacyTest {
    @Test
    fun masksShortSenderCompletely() {
        assertEquals("*", maskCommandSender("A"))
        assertEquals("****", maskCommandSender("1069"))
    }

    @Test
    fun keepsOnlyLastFourCharactersOfLongSender() {
        assertEquals("*******8000", maskCommandSender("13800138000"))
        assertEquals("未知发件人", maskCommandSender("   "))
    }
}