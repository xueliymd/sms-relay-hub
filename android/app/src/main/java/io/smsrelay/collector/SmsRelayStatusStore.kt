package io.smsrelay.collector

import android.content.Context
import java.time.Instant

class SmsRelayStatusStore(context: Context) {
    private val preferences = context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    fun record(result: String) {
        preferences.edit().putString(KEY_LAST_RESULT, "${Instant.now()} $result").apply()
    }

    fun lastResult(): String? = preferences.getString(KEY_LAST_RESULT, null)

    private companion object {
        const val PREFERENCES = "sms_relay_status"
        const val KEY_LAST_RESULT = "last_result"
    }
}
