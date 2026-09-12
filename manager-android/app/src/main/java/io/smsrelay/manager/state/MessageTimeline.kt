package io.smsrelay.manager.state

import io.smsrelay.manager.model.AdminMessage
import io.smsrelay.manager.time.MessageTime
import java.time.Instant

data class MessageCursor(
    val createdAt: String,
    val id: String,
)

class MessageTimeline(private val capacity: Int = 500) {
    private val messages = linkedMapOf<String, AdminMessage>()

    @Synchronized
    fun merge(incoming: List<AdminMessage>) {
        incoming.forEach { messages[it.id] = it }
        val ordered = ordered().take(capacity)
        messages.clear()
        ordered.forEach { messages[it.id] = it }
    }

    @Synchronized
    fun items(): List<AdminMessage> = ordered()

    @Synchronized
    fun cursor(): MessageCursor? = ordered().firstOrNull()?.let { MessageCursor(it.createdAt, it.id) }

    @Synchronized
    fun clear() {
        messages.clear()
    }

    @Synchronized
    fun remove(id: String) {
        messages.remove(id)
    }

    @Synchronized
    fun removeAll(ids: Collection<String>) {
        ids.forEach(messages::remove)
    }

    private fun ordered(): List<AdminMessage> = messages.values.sortedWith(
        compareByDescending<AdminMessage> { MessageTime.parse(it.createdAt) ?: Instant.MIN }
            .thenByDescending { it.createdAt }
            .thenByDescending { it.id },
    )
}
