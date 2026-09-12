package io.smsrelay.collector

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != Intent.ACTION_BOOT_COMPLETED && intent?.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        if (SmsRelayRuntime.config(context) == null) return
        // 用户在磁贴/主界面暂停转发时，开机与应用更新都不应擅自恢复。
        if (!RelaySwitch.isEnabled(context)) return
        // 开机/应用更新后：立即拉起前台服务，并登记周期看门狗。
        BackgroundRuntime.start(context)
        runCatching {
            KeepAliveWatchdog.enqueuePeriodicWorker(context)
        }
    }
}
