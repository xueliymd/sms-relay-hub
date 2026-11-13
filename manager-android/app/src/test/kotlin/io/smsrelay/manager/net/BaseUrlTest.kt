package io.smsrelay.manager.net

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class BaseUrlTest {
    @Test
    fun normalizesSupportedHttpAndHttpsOrigins() {
        assertEquals("https://sms.example.com", BaseUrl.parse("https://sms.example.com/").value)
        assertEquals("http://203.0.113.10:39300", BaseUrl.parse("http://203.0.113.10:39300").value)
        assertTrue(BaseUrl.parse("https://sms.example.com").secure)
        assertFalse(BaseUrl.parse("http://203.0.113.10:39300").secure)
    }

    @Test
    fun rejectsCredentialsPathsQueriesAndUnsupportedSchemes() {
        assertFailsWith<IllegalArgumentException> { BaseUrl.parse("ftp://example.com") }
        assertFailsWith<IllegalArgumentException> { BaseUrl.parse("https://user:pass@example.com") }
        assertFailsWith<IllegalArgumentException> { BaseUrl.parse("https://example.com/admin") }
        assertFailsWith<IllegalArgumentException> { BaseUrl.parse("https://example.com/?token=secret") }
    }

    @Test
    fun createsSameOriginHttpAndWebSocketEndpoints() {
        val base = BaseUrl.parse("https://sms.example.com")
        assertEquals("https://sms.example.com/api/v1/admin/runtime", base.http("/api/v1/admin/runtime"))
        assertEquals("wss://sms.example.com/api/v1/ws/admin/messages", base.webSocket("/api/v1/ws/admin/messages"))
    }
}
