package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotEquals

class CollectorIdentityStoreTest {
    @Test
    fun createsOneStableInstanceIdUntilExplicitlyCleared() {
        val values = mutableMapOf<String, String>()
        val storage = object : CollectorIdentityStorage {
            override fun read(key: String): String? = values[key]
            override fun write(key: String, value: String) {
                values[key] = value
            }
            override fun remove(key: String) {
                values.remove(key)
            }
        }
        val generated = ArrayDeque(listOf("instance-a", "instance-b"))
        val store = CollectorIdentityStore(storage) { generated.removeFirst() }

        assertEquals("instance-a", store.getOrCreate())
        assertEquals("instance-a", store.getOrCreate())
        store.clear()
        assertEquals("instance-b", store.getOrCreate())
        assertNotEquals("instance-a", store.getOrCreate())
    }
}
