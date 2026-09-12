package io.smsrelay.collector

import android.app.Application

class RelayApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        SmsRelayRuntime.initialize(this)
        // 进程被任何入口（开机/更新/磁贴/无障碍重连）拉起时，
        // 续登记周期看门狗，保证兜底链路不因进程重启而中断。
        // 但用户已在磁贴/主界面暂停转发时必须让路，否则"关"会被悄悄撤销。
        if (SmsRelayRuntime.config(this) != null && RelaySwitch.isEnabled(this)) {
            BackgroundRuntime.start(this)
            runCatching {
                KeepAliveWatchdog.enqueuePeriodicWorker(this)
            }
        }
    }
}
