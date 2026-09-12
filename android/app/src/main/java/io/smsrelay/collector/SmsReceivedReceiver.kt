package io.smsrelay.collector

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.provider.Telephony
import android.telephony.SmsMessage

class SmsReceivedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val broadcastReceivedAtMillis = System.currentTimeMillis()
        val broadcastReceivedElapsedNanos = SystemClock.elapsedRealtimeNanos()
        val messages = Telephony.Sms.Intents.getMessagesFromIntent(intent)
        if (messages.isEmpty()) return
        val sender = messages.firstNotNullOfOrNull { it.displayOriginatingAddress ?: it.originatingAddress } ?: "Unknown sender"
        val body = messages.joinToString(separator = "") { it.messageBody.orEmpty() }
        if (body.isBlank()) return
        val messageDate = messages.minOf { it.timestampMillis }
        val subscriptionHints = SmsSubscriptionHintResolver.resolve(
            extras = readSubscriptionExtras(intent),
            messageSubscriptionIds = messages.map(::readMessageSubscriptionId),
        )
        val pendingResult = goAsync()

        Thread {
            try {
                if (SmsRelayRuntime.config(context) == null) {
                    SmsRelayStatusStore(context).record("失败：尚未配置设备")
                    return@Thread
                }
                // SIM metadata is enrichment only. A vendor/telephony provider can
                // temporarily reject or omit SubscriptionInfo while the SMS itself
                // is already available. Never drop the message because enrichment
                // failed; the backend can reconcile the identity later from a
                // configured SIM profile.
                var subscriptionMetadataFailed = false
                val subscriptionSnapshot = try {
                    SmsRelayRuntime.captureSubscription(
                        context = context,
                        slotIndexHint = subscriptionHints.slotIndex,
                        subscriptionIdHint = subscriptionHints.subscriptionId,
                    )
                } catch (_: Exception) {
                    subscriptionMetadataFailed = true
                    null
                }
                val payload = SmsRelayPayload.fromSms(
                    sender = sender,
                    body = body,
                    messageDateMillis = messageDate,
                    receivedAtMillis = broadcastReceivedAtMillis,
                    // Preserve the broadcast slot even when optional
                    // SubscriptionInfo enrichment is unavailable.
                    simSlot = subscriptionHints.slotIndex,
                    subscriptionSnapshot = subscriptionSnapshot,
                    collectorInstanceId = CollectorIdentityStore(context).getOrCreate(),
                    collectorVersion = BuildConfig.VERSION_NAME,
                    collectorMode = "android_app",
                )
                val inserted = SmsRelayRuntime.repository(context).enqueue(payload)
                if (!inserted) {
                    SmsRelayStatusStore(context).record("重复消息已忽略")
                    return@Thread
                }
                // 用户已在磁贴/主界面暂停转发：短信仍写入本地队列（不丢数据），
                // 但不发起任何上报；恢复转发后由队列自动补传。
                // 没有这道闸门，"暂停"就只是停了前台服务，短信照样外发——开关名不副实。
                if (!RelaySwitch.isEnabled(context)) {
                    SmsRelayStatusStore(context).record("已加入本地队列（转发已暂停，恢复后自动补传）")
                    return@Thread
                }
                ImmediateUploadPayloadStore.offer(payload, broadcastReceivedElapsedNanos)
                SmsRelayStatusStore(context).record(
                    if (subscriptionMetadataFailed) {
                        "已加入同步队列（SIM元数据暂不可用）"
                    } else {
                        "已加入同步队列"
                    },
                )
                // Start the upload thread directly from the SMS broadcast path.
                // Do not start the dataSync foreground service again here. On Android 15/OPlus,
                // repeatedly starting that service from every SMS can exhaust the service time
                // budget and break later broadcasts. The persistent service is started by the
                // application/boot path; this fast path only needs the direct uploader.
                SmsRelayUploadRunner.triggerImmediate(context)
            } catch (_: Exception) {
                recordCaptureFailure(context)
            } finally {
                try {
                    pendingResult.finish()
                } catch (_: Exception) {
                    // The broadcast is already leaving this process; no sensitive details are recorded.
                }
            }
        }.start()
    }

    private fun recordCaptureFailure(context: Context) {
        try {
            SmsRelayStatusStore(context).record(CAPTURE_FAILURE_STATUS)
        } catch (_: Exception) {
            // Status persistence must not turn a handled capture failure into an uncaught exception.
        }
    }

    private fun readSubscriptionExtras(intent: Intent): Map<String, Any?> = buildMap {
        SmsSubscriptionHintResolver.relevantKeys().forEach { key ->
            val value = try {
                intent.extras?.get(key)
            } catch (_: SecurityException) {
                null
            } catch (_: RuntimeException) {
                null
            }
            if (value != null) put(key, value)
        }
    }

    private fun readMessageSubscriptionId(message: SmsMessage): Int? {
        val methodValue = listOf("getSubscriptionId", "getSubId").firstNotNullOfOrNull { methodName ->
            runCatching {
                message.javaClass.getMethod(methodName).invoke(message)
            }.getOrNull() as? Number
        }?.toInt()?.takeIf { it >= 0 }
        if (methodValue != null) return methodValue

        return listOf("mSubId", "mSubscriptionId").firstNotNullOfOrNull { fieldName ->
            runCatching {
                message.javaClass.getDeclaredField(fieldName).apply { isAccessible = true }.get(message)
            }.getOrNull()
                .let { it as? Number }
                ?.toInt()
                ?.takeIf { it >= 0 }
        }
    }

    private companion object {
        const val CAPTURE_FAILURE_STATUS = "SMS capture failed"
    }
}
