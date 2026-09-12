package io.smsrelay.manager.net

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import org.json.JSONObject

class AdminSocketProtocolTest {
    @Test
    fun buildsSeparateAuthenticationAndSubscriptionFrames() {
        val auth = JSONObject(AdminSocketProtocol.auth("session-token"))
        val subscription = JSONObject(AdminSocketProtocol.subscribe("device-1", 1))

        assertEquals("auth", auth.getString("type"))
        assertEquals("session-token", auth.getString("token"))
        assertEquals("subscribe", subscription.getString("type"))
        assertEquals("device-1", subscription.getString("device_id"))
        assertEquals(1, subscription.getInt("sim_slot"))
        assertEquals("ping", JSONObject(AdminSocketProtocol.ping()).getString("type"))

        val allDevices = JSONObject(AdminSocketProtocol.subscribe(null, null))
        assertEquals("subscribe", allDevices.getString("type"))
        assertFalse(allDevices.has("device_id"))
    }
}
