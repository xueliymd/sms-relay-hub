package io.smsrelay.collector

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.view.accessibility.AccessibilityEvent

class RelayKeepAliveAccessibilityService : AccessibilityService() {
    override fun onServiceConnected() {
        super.onServiceConnected()
        requestKeepAlive()
        // 无障碍服务由系统绑定：进程被杀后系统自动重连本服务，
        // 这里是"自动唤醒"的第一入口——顺手续登记周期看门狗。
        runCatching {
            KeepAliveWatchdog.enqueuePeriodicWorker(this)
        }
        // 记录"用户期望无障碍保活开启"，供周期看门狗在被系统关闭时自愈重启。
        getSharedPreferences(KEEP_ALIVE_PREFS, MODE_PRIVATE)
            .edit().putBoolean(KEY_A11Y_WANTED, true).apply()
        if (!KeepAliveOverlay.acquire(this, KeepAliveOverlay.accessibilityWindowType())) {
            android.widget.Toast.makeText(this, "添加无障碍保活窗口失败，可尝试重启无障碍", android.widget.Toast.LENGTH_SHORT).show()
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            requestKeepAlive()
        }
    }

    override fun onInterrupt() = Unit

    /**
     * 返回 true：无障碍服务被系统解绑（用户暂时关闭、系统回收）后，
     * 系统会在满足条件时自动重新绑定本服务，而不是彻底销毁。
     * 这是无障碍服务区别于普通 Service 的"自动复活"能力，
     * 也是进程被杀后能第一时间回来的关键兜底。
     */
    override fun onUnbind(intent: Intent?): Boolean = true

    override fun onDestroy() {
        KeepAliveOverlay.release(this, KeepAliveOverlay.accessibilityWindowType())
        super.onDestroy()
    }

    private fun requestKeepAlive() {
        // 用户已暂停转发时不再重申前台服务，避免"关了又被拉起来"。
        if (!RelaySwitch.isEnabled(this)) return
        runCatching { BackgroundRuntime.ensureKeepAlive(this) }
    }

    private companion object {
        const val KEEP_ALIVE_PREFS = "keep_alive_prefs"
        const val KEY_A11Y_WANTED = "a11y_wanted"
    }
}
