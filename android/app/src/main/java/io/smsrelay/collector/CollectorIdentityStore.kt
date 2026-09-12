package io.smsrelay.collector

import android.content.Context
import java.util.UUID

interface CollectorIdentityStorage {
    fun read(key: String): String?
    fun write(key: String, value: String)
    fun remove(key: String)
}

class CollectorIdentityStore(
    private val storage: CollectorIdentityStorage,
    private val idFactory: () -> String = { UUID.randomUUID().toString() },
) {
    constructor(context: Context) : this(
        object : CollectorIdentityStorage {
            private val preferences = context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

            override fun read(key: String): String? = preferences.getString(key, null)
            override fun write(key: String, value: String) {
                preferences.edit().putString(key, value).apply()
            }
            override fun remove(key: String) {
                preferences.edit().remove(key).apply()
            }
        },
    )

    fun getOrCreate(): String {
        storage.read(KEY_INSTANCE_ID)?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        return idFactory().also { storage.write(KEY_INSTANCE_ID, it) }
    }

    fun clear() {
        storage.remove(KEY_INSTANCE_ID)
    }

    private companion object {
        const val PREFERENCES = "sms_relay_collector_identity"
        const val KEY_INSTANCE_ID = "collector_instance_id"
    }
}
