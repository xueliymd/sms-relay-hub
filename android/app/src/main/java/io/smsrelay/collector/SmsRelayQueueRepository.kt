package io.smsrelay.collector

import android.content.Context
import java.util.concurrent.Callable

class SmsRelayQueueRepository(context: Context) {
    private val database = SmsRelayDatabase.get(context)
    private val dao = database.queueDao()
    private val cipher = SmsRelayLocalCipher()
    private val collectorInstanceId = CollectorIdentityStore(context).getOrCreate()

    fun enqueue(payload: SmsRelayPayload): Boolean = database.runInTransaction(
        Callable {
            val phoneNumber = payload.metadata["phone_number"]?.trim()?.takeIf { it.isNotEmpty() }
            val inserted = dao.insert(
                SmsRelayQueueEntity(
                    clientMessageId = payload.clientMessageId,
                    receivedAt = payload.receivedAt,
                    senderCiphertext = cipher.seal(payload.sender),
                    bodyCiphertext = cipher.seal(payload.body),
                    simSlot = payload.simSlot,
                    subscriptionId = payload.metadata["subscription_id"]?.toIntOrNull(),
                    carrierName = payload.metadata["carrier_name"]?.trim()?.takeIf { it.isNotEmpty() },
                    phoneNumberCiphertext = if (phoneNumber != null) cipher.seal(phoneNumber) else null,
                ),
            ) > 0
            val overflow = dao.countAll() - MAX_QUEUE_SIZE
            if (overflow > 0) dao.deleteOldest(overflow)
            inserted
        },
    )

    fun pending(limit: Int = BATCH_SIZE): List<SmsRelayQueueEntity> = dao.pending(limit)

    fun pendingCount(): Int = dao.countPending()

    fun toPayload(entity: SmsRelayQueueEntity): SmsRelayPayload {
        val sender = requireNotNull(entity.senderCiphertext) { "sender was already redacted" }
        val body = requireNotNull(entity.bodyCiphertext) { "body was already redacted" }
        val phoneNumber = if (entity.phoneNumberCiphertext != null) {
            cipher.open(entity.phoneNumberCiphertext)
        } else {
            null
        }
        return SmsRelayPayload(
            schemaVersion = 1,
            clientMessageId = entity.clientMessageId,
            receivedAt = entity.receivedAt,
            sender = cipher.open(sender),
            body = cipher.open(body),
            simSlot = entity.simSlot,
            metadata = SmsRelayPayload.metadataFor(
                subscriptionSnapshot = SubscriptionSnapshot(
                    slotIndex = entity.simSlot,
                    subscriptionId = entity.subscriptionId,
                    carrierName = entity.carrierName,
                    phoneNumber = phoneNumber,
                ),
                collectorInstanceId = collectorInstanceId,
                collectorVersion = BuildConfig.VERSION_NAME,
                collectorMode = "android_app",
            ),
        )
    }

    fun markUploaded(entity: SmsRelayQueueEntity) {
        dao.finish(entity.rowId, QueueState.UPLOADED, null, null)
    }

    fun markUploadedByClientMessageId(clientMessageId: String) {
        dao.finishByClientMessageId(clientMessageId, QueueState.UPLOADED, null, null)
    }

    fun markConflict(entity: SmsRelayQueueEntity) {
        dao.finish(entity.rowId, QueueState.CONFLICT, null, "idempotency_conflict")
    }

    fun markConflictByClientMessageId(clientMessageId: String) {
        dao.finishByClientMessageId(clientMessageId, QueueState.CONFLICT, null, "idempotency_conflict")
    }

    fun markPermanentFailure(entity: SmsRelayQueueEntity, reason: String) {
        dao.finish(entity.rowId, QueueState.PERMANENT_FAILURE, null, reason)
    }

    fun markPermanentFailureByClientMessageId(clientMessageId: String, reason: String) {
        dao.finishByClientMessageId(clientMessageId, QueueState.PERMANENT_FAILURE, null, reason)
    }

    fun markAuthBlocked(entity: SmsRelayQueueEntity) {
        dao.markAuthBlocked(entity.rowId, "credential_rejected")
    }

    fun markAuthBlockedByClientMessageId(clientMessageId: String) {
        dao.markAuthBlockedByClientMessageId(clientMessageId, "credential_rejected")
    }

    fun markRetry(entity: SmsRelayQueueEntity) {
        dao.markRetry(entity.rowId, "transient_failure")
    }

    fun markRetryByClientMessageId(clientMessageId: String) {
        dao.markRetryByClientMessageId(clientMessageId, "transient_failure")
    }

    fun resumeAfterCredentialUpdate(): Int = dao.resumeAuthBlocked()

    private companion object {
        const val MAX_QUEUE_SIZE = 500
        const val BATCH_SIZE = 50
    }
}
