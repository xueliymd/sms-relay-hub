package io.smsrelay.collector

import android.content.Context
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

/**
 * 保活看门狗：WorkManager 周期任务（最坏 15 分钟兜底）。
 *
 * 任务持久化于系统、重启后仍在；周期唤醒进程后重启前台服务。
 * 主要复活锚点是无障碍服务的系统级重连（对齐 GKD）；本任务仅作
 * 无障碍未开启场景下的低成本兜底（系统批量调度，耗电极低）。
 */
object KeepAliveWatchdog {
    private const val PERIODIC_WORK_NAME = "sms-relay-keep-alive"

    /** 登记周期看门狗；KEEP 策略避免重复登记覆盖下次执行时间。 */
    fun enqueuePeriodicWorker(context: Context) {
        // 用户已暂停转发时不再登记：否则磁贴的"关闭"会被下一次登记悄悄撤销。
        if (!RelaySwitch.isEnabled(context)) return
        val request = PeriodicWorkRequestBuilder<KeepAliveWorker>(15, TimeUnit.MINUTES).build()
        WorkManager.getInstance(context.applicationContext)
            .enqueueUniquePeriodicWork(PERIODIC_WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request)
    }

    /** 取消周期看门狗，与 enqueuePeriodicWorker 配对，供磁贴"关闭"路径调用。 */
    fun cancel(context: Context) {
        WorkManager.getInstance(context.applicationContext)
            .cancelUniqueWork(PERIODIC_WORK_NAME)
    }
}
