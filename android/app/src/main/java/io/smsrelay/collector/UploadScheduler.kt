package io.smsrelay.collector

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

object UploadScheduler {
    private const val WORK_NAME = "sms-relay-upload"

    fun enqueue(context: Context) {
        val request = OneTimeWorkRequestBuilder<SmsRelayUploadWorker>()
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build(),
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context.applicationContext)
            // KEEP can preserve an ENQUEUED work whose scheduler request was lost
            // (observed as schedule_requested_at=-1), leaving pending rows stranded.
            // This is one unique retry pipeline, so replacing it is safe and makes
            // every retry request schedulable again.
            .enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.REPLACE, request)
    }
}
