package io.smsrelay.collector

import android.content.Context

/**
 * 用户层"转发开关"：通知栏磁贴与主界面都可切换。
 *
 * 关闭后，所有**自动拉起**链路都会让路 —— 进程启动、开机/更新、无障碍重连、
 * 周期看门狗都不再重新拉起前台服务。否则"关掉"只维持到下一封短信或下一次
 * 进程重启，用户会认为按钮是坏的（这正是磁贴"点了不能关"的一半原因）。
 *
 * 显式用户动作（保存配置、扫码配置）视为"开启并立即生效"。
 */
object RelaySwitch {
    private const val PREFS = "relay_switch"
    private const val KEY_ENABLED = "enabled"

    /** 默认开启：未设置过时保持既有行为，不影响老用户升级。 */
    fun isEnabled(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getBoolean(KEY_ENABLED, true)

    fun setEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putBoolean(KEY_ENABLED, enabled).apply()
    }
}
