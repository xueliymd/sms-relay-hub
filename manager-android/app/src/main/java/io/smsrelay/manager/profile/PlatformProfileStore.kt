package io.smsrelay.manager.profile

import android.content.Context
import io.smsrelay.manager.model.PlatformProfile
import io.smsrelay.manager.security.AndroidKeyStoreSecretCipher
import io.smsrelay.manager.security.SecretCipher
import org.json.JSONArray
import org.json.JSONObject

interface ProfileStorage {
    fun get(key: String): String?
    fun put(key: String, value: String)
    fun remove(key: String)
}

class PlatformProfileStore(
    private val storage: ProfileStorage,
    private val cipher: SecretCipher,
) {
    constructor(context: Context) : this(
        object : ProfileStorage {
            private val preferences = context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            override fun get(key: String): String? = preferences.getString(key, null)
            override fun put(key: String, value: String) {
                preferences.edit().putString(key, value).apply()
            }
            override fun remove(key: String) {
                preferences.edit().remove(key).apply()
            }
        },
        AndroidKeyStoreSecretCipher(),
    )

    @Synchronized
    fun list(): List<PlatformProfile> {
        val raw = storage.get(KEY_PROFILES) ?: return emptyList()
        return runCatching {
            val array = JSONArray(raw)
            buildList {
                for (index in 0 until array.length()) {
                    val item = array.getJSONObject(index)
                    add(
                        PlatformProfile(
                            id = item.getString("id"),
                            label = item.getString("label"),
                            baseUrl = item.getString("base_url"),
                            username = item.getString("username"),
                            rememberPassword = item.optBoolean("remember_password", false),
                        ),
                    )
                }
            }
        }.getOrDefault(emptyList())
    }

    @Synchronized
    fun save(profile: PlatformProfile, password: String?, sessionToken: String?) {
        require(profile.id.isNotBlank()) { "profile id is required" }
        val profiles = list().associateByTo(linkedMapOf()) { it.id }
        profiles[profile.id] = profile
        persistProfiles(profiles.values.toList())
        when {
            !profile.rememberPassword -> storage.remove(passwordKey(profile.id))
            password == null -> Unit
            password.isEmpty() -> storage.remove(passwordKey(profile.id))
            else -> storage.put(passwordKey(profile.id), cipher.seal(password))
        }
        if (!sessionToken.isNullOrEmpty()) storage.put(sessionKey(profile.id), cipher.seal(sessionToken))
    }

    @Synchronized
    fun delete(profileId: String) {
        persistProfiles(list().filterNot { it.id == profileId })
        storage.remove(passwordKey(profileId))
        storage.remove(sessionKey(profileId))
        if (storage.get(KEY_SELECTED_PROFILE) == profileId) storage.remove(KEY_SELECTED_PROFILE)
    }

    fun select(profileId: String) {
        require(list().any { it.id == profileId }) { "unknown profile" }
        storage.put(KEY_SELECTED_PROFILE, profileId)
    }

    fun selected(): PlatformProfile? {
        val selectedId = storage.get(KEY_SELECTED_PROFILE) ?: return null
        return list().firstOrNull { it.id == selectedId }
    }

    fun password(profileId: String): String? = decrypt(storage.get(passwordKey(profileId)))

    fun sessionToken(profileId: String): String? = decrypt(storage.get(sessionKey(profileId)))

    fun clearSession(profileId: String) {
        storage.remove(sessionKey(profileId))
    }

    private fun decrypt(value: String?): String? = value?.let { runCatching { cipher.open(it) }.getOrNull() }

    private fun persistProfiles(profiles: List<PlatformProfile>) {
        val array = JSONArray()
        profiles.forEach { profile ->
            array.put(
                JSONObject()
                    .put("id", profile.id)
                    .put("label", profile.label)
                    .put("base_url", profile.baseUrl)
                    .put("username", profile.username)
                    .put("remember_password", profile.rememberPassword),
            )
        }
        storage.put(KEY_PROFILES, array.toString())
    }

    private fun passwordKey(profileId: String): String = "profile.password." + profileId
    private fun sessionKey(profileId: String): String = "profile.session." + profileId

    private companion object {
        const val PREFERENCES = "relay_hub_manager_profiles"
        const val KEY_PROFILES = "profiles"
        const val KEY_SELECTED_PROFILE = "selected_profile"
    }
}
