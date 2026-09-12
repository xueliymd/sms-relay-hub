package io.smsrelay.collector

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters

class SmsRelayUploadWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result = when (SmsRelayUploadRunner.runOnce(applicationContext)) {
        SmsRelayUploadRunner.RunResult.Success -> Result.success()
        SmsRelayUploadRunner.RunResult.Retryable -> Result.retry()
        SmsRelayUploadRunner.RunResult.AuthBlocked -> Result.failure()
    }
}
