package io.smsrelay.manager.profile

import io.smsrelay.manager.model.PlatformProfile
import io.smsrelay.manager.security.SecretCipher
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class PlatformProfileStoreTest {
    @Test
    fun storesMultipleProfilesAndSelectsOne() {
        val fixture = fixture()
        fixture.store.save(profile("one", remember = false), password = "ignored", sessionToken = null)
        fixture.store.save(profile("two", remember = true), password = "secret-two", sessionToken = "session-two")
        fixture.store.select("two")

        assertEquals(listOf("one", "two"), fixture.store.list().map { it.id })
        assertEquals("two", fixture.store.selected()?.id)
        assertNull(fixture.store.password("one"))
        assertEquals("secret-two", fixture.store.password("two"))
        assertEquals("session-two", fixture.store.sessionToken("two"))
        assertFalse(fixture.values.values.any { it == "secret-two" || it == "session-two" })
        assertTrue(fixture.values.values.any { it.contains("sealed:") })
    }

    @Test
    fun updateWithoutRememberingPasswordDeletesOldPassword() {
        val fixture = fixture()
        fixture.store.save(profile("one", remember = true), password = "secret-one", sessionToken = "session-one")
        fixture.store.save(profile("one", remember = false), password = "new-secret", sessionToken = null)

        assertNull(fixture.store.password("one"))
        assertEquals("session-one", fixture.store.sessionToken("one"))
        assertEquals("admin-one", fixture.store.list().single().username)
    }

    @Test
    fun clearingRememberedPasswordDeletesOldEncryptedValue() {
        val fixture = fixture()
        fixture.store.save(profile("one", remember = true), password = "secret-one", sessionToken = null)
        fixture.store.save(profile("one", remember = true), password = "", sessionToken = null)

        assertNull(fixture.store.password("one"))
    }

    @Test
    fun deletingProfileClearsEncryptedSecretsAndSelection() {
        val fixture = fixture()
        fixture.store.save(profile("one", remember = true), password = "secret-one", sessionToken = "session-one")
        fixture.store.select("one")
        fixture.store.delete("one")

        assertTrue(fixture.store.list().isEmpty())
        assertNull(fixture.store.selected())
        assertNull(fixture.store.password("one"))
        assertNull(fixture.store.sessionToken("one"))
        assertFalse(fixture.values.keys.any { it.contains("one") })
    }

    private fun profile(id: String, remember: Boolean) = PlatformProfile(
        id = id,
        label = "Platform $id",
        baseUrl = "https://$id.example.com",
        username = "admin-$id",
        rememberPassword = remember,
    )

    private fun fixture(): Fixture {
        val values = linkedMapOf<String, String>()
        val storage = object : ProfileStorage {
            override fun get(key: String): String? = values[key]
            override fun put(key: String, value: String) {
                values[key] = value
            }
            override fun remove(key: String) {
                values.remove(key)
            }
        }
        val cipher = object : SecretCipher {
            override fun seal(value: String): String = "sealed:$value"
            override fun open(value: String): String = value.removePrefix("sealed:")
        }
        return Fixture(PlatformProfileStore(storage, cipher), values)
    }

    private data class Fixture(
        val store: PlatformProfileStore,
        val values: MutableMap<String, String>,
    )
}
