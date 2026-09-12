package io.smsrelay.collector

import android.content.Context
import android.os.PowerManager
import android.os.SystemClock
import java.util.concurrent.atomic.AtomicBoolean

/** Runs one upload pass immediately; WorkManager remains the retry/backoff path. */
object SmsRelayUploadRunner {
    private val running = AtomicBoolean(false)
    private val rerunRequested = AtomicBoolean(false)

    fun trigger(context: Context) {
        triggerInternal(context, immediateOnly = false)
    }

    fun triggerImmediate(context: Context) {
        triggerInternal(context, immediateOnly = true)
    }

    private fun triggerInternal(context: Context, immediateOnly: Boolean) {
        rerunRequested.set(true)
        if (!running.compareAndSet(false, true)) return
        val appContext = context.applicationContext
        Thread {
            val powerManager = appContext.getSystemService(PowerManager::class.java)
            val wakeLock = powerManager?.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "RelaySms:immediateUpload",
            )
            try {
                wakeLock?.acquire(UPLOAD_WAKE_LOCK_TIMEOUT_MS)
                do {
                    rerunRequested.set(false)
                    when (if (immediateOnly) runImmediate(appContext) else runOnce(appContext)) {
                        RunResult.Retryable -> {
                            UploadScheduler.enqueue(appContext)
                    return@Thread
                        }
                        RunResult.AuthBlocked -> return@Thread
                        RunResult.Success -> Unit
                    }
                } while (rerunRequested.get())
            } finally {
                if (wakeLock?.isHeld == true) wakeLock.release()
                running.set(false)
                // 顺带拉取远程控制指令（失败静默），复用本轮已唤醒的进程与网络
                DeviceCommandPoller.poll(appContext)
                if (rerunRequested.get()) triggerInternal(appContext, immediateOnly)
            }
        }.apply {
            name = "sms-relay-upload-now"
            isDaemon = true
            start()
        }
    }

    /** Uploads newly captured SMS payloads without rereading/decrypting the queue first. */
    fun runImmediate(context: Context): RunResult {
        // 用户已暂停转发：不消费内存队列、不发请求。恢复后由持久队列补传。
        if (!RelaySwitch.isEnabled(context)) return RunResult.Success
        val config = SmsRelayRuntime.config(context) ?: return RunResult.AuthBlocked
        val repository = SmsRelayRuntime.repository(context)
        val client = SmsRelayHttpClient()
        var uploadedCount = 0
        var lastUploadMillis = 0L
        var lastBroadcastToResponseMillis = 0L

        while (true) {
            val item = ImmediateUploadPayloadStore.poll() ?: break
            val payload = item.payload
            val outcome = uploadPayload(client, config, payload) { lastUploadMillis = it }
            lastBroadcastToResponseMillis = UploadTiming.elapsedMillis(
                item.broadcastReceivedElapsedNanos,
                SystemClock.elapsedRealtimeNanos(),
            )
            when (outcome) {
                UploadOutcome.Uploaded -> {
                    repository.markUploadedByClientMessageId(payload.clientMessageId)
                    uploadedCount += 1
                }
                UploadOutcome.Conflict -> {
                    repository.markConflictByClientMessageId(payload.clientMessageId)
                    uploadedCount += 1
                }
                UploadOutcome.AuthBlocked -> {
                    ImmediateUploadPayloadStore.clear()
                    repository.markAuthBlockedByClientMessageId(payload.clientMessageId)
                    SmsRelayStatusStore(context).record("暂停：设备凭据已失效")
                    return RunResult.AuthBlocked
                }
                UploadOutcome.Retryable -> {
                    ImmediateUploadPayloadStore.clear()
                    repository.markRetryByClientMessageId(payload.clientMessageId)
                    SmsRelayStatusStore(context).record("等待网络重试")
                    return RunResult.Retryable
                }
                UploadOutcome.PermanentFailure -> {
                    repository.markPermanentFailureByClientMessageId(payload.clientMessageId, "request_rejected")
                }
            }
        }

        if (repository.pendingCount() > 0) UploadScheduler.enqueue(context)
        SmsRelayStatusStore(context).record(
            "队列已同步（${uploadedCount} 条，广播到响应 ${lastBroadcastToResponseMillis}ms，HTTP ${lastUploadMillis}ms）",
        )
        return RunResult.Success
    }

    fun runOnce(context: Context): RunResult {
        // 用户已暂停转发：本地队列保留（pending 不消费），恢复后由
        // BackgroundRuntime.start → UploadScheduler 重新排空。
        if (!RelaySwitch.isEnabled(context)) return RunResult.Success
        val config = SmsRelayRuntime.config(context) ?: return RunResult.AuthBlocked
        val repository = SmsRelayRuntime.repository(context)
        val client = SmsRelayHttpClient()
        var uploadedCount = 0
        var lastUploadMillis = 0L
        val pending = repository.pending()
        if (pending.isEmpty()) return RunResult.Success

        for (entity in pending) {
            val payloadResult = runCatching { repository.toPayload(entity) }
            if (payloadResult.isFailure) {
                repository.markPermanentFailure(entity, "local_decryption_failed")
                continue
            }
            val payload = payloadResult.getOrThrow()
            val outcome = uploadPayload(client, config, payload) { lastUploadMillis = it }
            when (outcome) {
                UploadOutcome.Uploaded -> {
                    repository.markUploaded(entity)
                    uploadedCount += 1
                }
                UploadOutcome.Conflict -> repository.markConflict(entity)
                UploadOutcome.AuthBlocked -> {
                    repository.markAuthBlocked(entity)
                    SmsRelayStatusStore(context).record("暂停：设备凭据已失效")
                    return RunResult.AuthBlocked
                }
                UploadOutcome.Retryable -> {
                    repository.markRetry(entity)
                    SmsRelayStatusStore(context).record("等待网络重试")
                    return RunResult.Retryable
                }
                UploadOutcome.PermanentFailure -> repository.markPermanentFailure(entity, "request_rejected")
            }
        }
        SmsRelayStatusStore(context).record("队列已同步（${uploadedCount} 条，HTTP ${lastUploadMillis}ms）")
        return if (repository.pendingCount() > 0) RunResult.Retryable else RunResult.Success
    }

    private fun uploadPayload(
        client: SmsRelayHttpClient,
        config: SmsRelayConfig,
        payload: SmsRelayPayload,
        onTiming: (Long) -> Unit,
    ): UploadOutcome {
        val response = runCatching { client.post(config, payload) }
            .onSuccess { onTiming(it.elapsedMillis) }
        return runCatching { UploadOutcome.fromStatus(response.getOrThrow().statusCode) }
            .getOrDefault(UploadOutcome.Retryable)
    }

    enum class RunResult { Success, Retryable, AuthBlocked }

    private const val UPLOAD_WAKE_LOCK_TIMEOUT_MS = 30_000L
}
