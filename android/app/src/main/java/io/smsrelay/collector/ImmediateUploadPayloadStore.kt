package io.smsrelay.collector

import java.util.concurrent.ConcurrentLinkedQueue

data class ImmediateUploadItem(
    val payload: SmsRelayPayload,
    val broadcastReceivedElapsedNanos: Long,
)

/** Holds newly captured payloads until the already-running service drains them. */
object ImmediateUploadPayloadStore {
    private val payloads = ConcurrentLinkedQueue<ImmediateUploadItem>()

    fun offer(payload: SmsRelayPayload, broadcastReceivedElapsedNanos: Long) {
        payloads.offer(ImmediateUploadItem(payload, broadcastReceivedElapsedNanos))
    }

    fun poll(): ImmediateUploadItem? = payloads.poll()

    fun take(clientMessageId: String): ImmediateUploadItem? {
        val iterator = payloads.iterator()
        while (iterator.hasNext()) {
            val item = iterator.next()
            if (item.payload.clientMessageId == clientMessageId && payloads.remove(item)) return item
        }
        return null
    }

    fun clear() {
        payloads.clear()
    }

}
