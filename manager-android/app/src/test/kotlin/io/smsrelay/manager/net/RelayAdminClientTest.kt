package io.smsrelay.manager.net

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.OkHttpClient
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class RelayAdminClientTest {
    private lateinit var server: MockWebServer
    private lateinit var client: RelayAdminClient

    @BeforeTest
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = RelayAdminClient(BaseUrl.parse(server.url("/").toString()))
    }

    @AfterTest
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun loginDoesNotSendAdminTokenAndParsesSession() {
        server.enqueue(json("""{"token":"session-token","username":"admin","expires_at":"2026-09-01T18:00:00Z"}"""))

        val session = client.login("admin", "safe-password")
        val request = server.takeRequest()

        assertEquals("session-token", session.token)
        assertEquals("/api/v1/admin/login", request.path)
        assertEquals("POST", request.method)
        assertFalse(request.headers.names().contains("X-Admin-Token"))
        assertTrue(request.body.readUtf8().contains("safe-password"))
    }

    @Test
    fun loadsRuntimeDevicesSimMessagesAndReleaseHistory() {
        server.enqueue(json("""{"database_backend":"sqlite","database_fingerprint":"db","platform_version":"1.0.11","public_base_url":"https://relay.example.com","relay_site":"relay.example.com","http_port":80,"https_port":443,"app_port":39300,"https_enabled":true,"services":{"api":"healthy"},"server_time":"2026-09-01T08:00:00Z"}"""))
        server.enqueue(json("""[{"id":"device-1","name":"Phone","client_type":"android_app","active":true,"online":true,"last_seen_at":"2026-09-01T08:00:00Z","client_instance_id":"instance-a","client_version":"1.0.11","client_mode":"android_app","credential_reuse_warning":false,"duplicate_collection_warning":false}]"""))
        server.enqueue(json("""[{"device_id":"device-1","slot_index":1,"subscription_id":42,"phone_number_masked":"******1234","carrier_name":"Carrier","source":"auto","updated_at":"2026-09-01T08:00:00Z"}]"""))
        server.enqueue(json("""[{"id":"message-1","device_id":"device-1","received_at":"2026-09-01T08:00:00Z","created_at":"2026-09-01T08:00:01Z","message_date":"2026-09-01T07:59:58Z","sender":"1069","body":"code 482731","source":"android_app","sim_slot":1,"subscription_id":42,"receiving_phone_masked":"******1234","receiving_identity_status":"resolved","otp_code":"482731"}]"""))
        server.enqueue(json("""{"releases":[{"platform":"android","channel":"stable","version_code":12,"version_name":"1.0.11","published_at":"2026-09-01T05:12:06Z","notes":"stable","size_bytes":2211177,"sha256":"abc","download_url":"/release.apk"}]}"""))

        assertEquals("1.0.11", client.runtime("session-token").platformVersion)
        assertTrue(client.devices("session-token").single().online)
        assertEquals(1, client.simProfiles("session-token", "device-1").single().slotIndex)
        val message = client.messages("session-token", "device-1", 1, null).single()
        assertEquals("482731", message.otpCode)
        assertEquals("2026-09-01T07:59:58Z", message.messageDate)
        assertEquals("1.0.11", client.releaseHistory("android").single().versionName)

        val requests = (1..5).map { server.takeRequest() }
        assertTrue(requests.take(4).all { it.getHeader("X-Admin-Token") == "session-token" })
        assertEquals(null, requests.last().getHeader("X-Admin-Token"))
        assertTrue(requests[3].path.orEmpty().contains("device_id=device-1"))
        assertTrue(requests[3].path.orEmpty().contains("sim_slot=1"))
    }

    @Test
    fun mapsUnauthorizedAndInvalidJsonWithoutLeakingResponseBody() {
        server.enqueue(MockResponse().setResponseCode(401).setBody("wrong password details"))
        val unauthorized = assertFailsWith<AdminClientException.Unauthorized> { client.runtime("expired") }
        assertTrue(unauthorized.url.endsWith("/api/v1/admin/runtime"))

        server.enqueue(json("not-json"))
        val invalidResponse = assertFailsWith<AdminClientException.InvalidResponse> { client.devices("session") }
        assertTrue(invalidResponse.url.endsWith("/api/v1/admin/devices"))
        assertFalse(invalidResponse.message!!.contains("wrong password details"))
    }

    @Test
    fun mapsNetworkFailureToRequestUrl() {
        val url = server.url("/").toString()
        server.shutdown()

        val failure = assertFailsWith<AdminClientException.NetworkFailure> {
            RelayAdminClient(BaseUrl.parse(url), OkHttpClient()).runtime("session-token")
        }

        assertTrue(failure.url.endsWith("/api/v1/admin/runtime"))
    }

    @Test
    fun acceptsRuntimeWithoutPublicBaseUrl() {
        server.enqueue(json("""{"database_backend":"sqlite","database_fingerprint":"db","platform_version":"1.0.11","relay_site":":80","http_port":80,"https_port":443,"app_port":39300,"https_enabled":false,"services":{"api":"healthy"},"server_time":"2026-09-02T04:00:00Z"}"""))

        assertEquals("", client.runtime("session-token").publicBaseUrl)
    }

    @Test
    fun supportsManagerReleaseHistory() {
        server.enqueue(json("""{"releases":[{"platform":"manager","channel":"stable","version_code":1,"version_name":"1.0.0","published_at":"2026-09-01T10:30:00Z","notes":"manager","size_bytes":4200000,"sha256":"abc","download_url":"/manager.apk"}]}"""))

        val release = client.releaseHistory("manager").single()

        assertEquals("manager", release.platform)
        assertEquals("/api/v1/releases/manager/history?channel=stable", server.takeRequest().path)
    }

    @Test
    fun listsMessagesAcrossAllDevicesWhenDeviceFilterIsAbsent() {
        server.enqueue(json("[]"))

        assertTrue(client.messages("session-token", null, null, null).isEmpty())

        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertFalse(request.path.orEmpty().contains("device_id="))
        assertTrue(request.path.orEmpty().contains("limit=500"))
    }

    @Test
    fun acceptsMessagesWithBlankSender() {
        server.enqueue(json("""[{"id":"message-1","device_id":"device-1","received_at":"2026-09-01T08:00:00Z","created_at":"2026-09-01T08:00:01Z","sender":"","body":"code 482731","source":"android_app","sim_slot":null,"subscription_id":null,"receiving_phone_masked":null,"receiving_identity_status":"unresolved"}]"""))

        val message = client.messages("session-token", null, null, null).single()

        assertEquals("未知发件人", message.sender)
    }

    private fun json(body: String) = MockResponse()
        .setResponseCode(200)
        .setHeader("Content-Type", "application/json")
        .setBody(body)
}
