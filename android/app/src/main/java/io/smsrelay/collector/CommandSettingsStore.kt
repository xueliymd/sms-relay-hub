package io.smsrelay.collector

import android.content.Context

/**
 * 远程控制指令的端侧授权开关（默认全部关闭）。
 *
 * 安全模型：平台下发指令不代表设备必须执行。每类指令需用户在本机显式开启，
 * 关闭时设备只回传"未授权"说明，不会读取任何数据。默认全关，
 * 避免拿到设备凭据即可远程读短信/电量。
 */
class CommandSettingsStore(context: Context) {
    private val preferences = context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    fun isAuthorized(commandType: String): Boolean = preferences.getBoolean(keyFor(commandType), false)

    fun setAuthorized(commandType: String, authorized: Boolean) {
        preferences.edit().putBoolean(keyFor(commandType), authorized).apply()
    }

    private fun keyFor(commandType: String): String = when (commandType) {
        COMMAND_QUERY_BATTERY -> KEY_BATTERY
        COMMAND_QUERY_RECENT_SMS -> KEY_RECENT_SMS
        COMMAND_TRIGGER_UPLOAD -> KEY_TRIGGER_UPLOAD
        else -> "unauthorized_$commandType"
    }

    companion object {
        const val COMMAND_QUERY_BATTERY = "query_battery"
        const val COMMAND_QUERY_RECENT_SMS = "query_recent_sms"
        const val COMMAND_TRIGGER_UPLOAD = "trigger_upload"

        val SUPPORTED_COMMANDS = listOf(
            COMMAND_QUERY_BATTERY,
            COMMAND_QUERY_RECENT_SMS,
            COMMAND_TRIGGER_UPLOAD,
        )

        private const val PREFERENCES = "sms_relay_commands"
        private const val KEY_BATTERY = "authorize_query_battery"
        private const val KEY_RECENT_SMS = "authorize_query_recent_sms"
        private const val KEY_TRIGGER_UPLOAD = "authorize_trigger_upload"
    }
}
