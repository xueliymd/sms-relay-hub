package io.smsrelay.collector

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * 周期保活任务：进程死亡后由 WorkManager 在最坏 15 分钟内唤醒本进程，
 * 若转发配置仍在且用户未暂停，则重新拉起前台服务（自动唤醒的最后兜底）。
 *
 * 注：心跳闹钟已在保活精简中移除，本任务只负责"拉回前台服务"。
 */
class KeepAliveWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val context = applicationContext
        // 用户已暂停转发：本任务保持静默，不做任何拉起动作。
        if (!RelaySwitch.isEnabled(context)) return Result.success()
        if (SmsRelayRuntime.config(context) != null) {
            BackgroundRuntime.start(context)
            // 周期唤醒顺带拉取远程控制指令，保证没有新短信时指令也能在 15 分钟内被取走
            DeviceCommandPoller.poll(context)
            // 无障碍自愈（对齐 GKD fixA11yService）：仅当用户曾主动开启过无障碍保活、
            // 且持有 WRITE_SECURE_SETTINGS 时才自动重启，避免在用户不想用时反复强制开启。
            //
            // restart() 内部有 1 秒重绑等待（Thread.sleep）；这里是 suspend 上下文，
            // 必须切到 IO 线程执行，否则会阻塞 WorkManager 的协程调度线程。
            runCatching {
                val wanted = context.getSharedPreferences(KEEP_ALIVE_PREFS, Context.MODE_PRIVATE)
                    .getBoolean(KEY_A11Y_WANTED, false)
                if (wanted &&
                    AccessibilityAutoToggle.canWriteSecureSettings(context) &&
                    !AccessibilityKeepAlive.isEnabled(context)
                ) {
                    withContext(Dispatchers.IO) {
                        AccessibilityAutoToggle.restart(context)
                    }
                }
            }
        }
        return Result.success()
    }

    private companion object {
        const val KEEP_ALIVE_PREFS = "keep_alive_prefs"
        const val KEY_A11Y_WANTED = "a11y_wanted"
    }
}
