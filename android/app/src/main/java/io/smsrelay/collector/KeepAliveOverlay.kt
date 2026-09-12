package io.smsrelay.collector

import android.content.Context
import android.graphics.PixelFormat
import android.os.Build
import android.view.Gravity
import android.view.View
import android.view.WindowManager

/**
 * 1x1 像素全透明悬浮窗保活（与 GKD 的 KeepAliveOverlay 同款思路）。
 *
 * 持有窗口的进程在 WindowManager 里有活跃窗口对象，多数 ROM 的空闲查杀
 * 会跳过这类进程；无障碍层（TYPE_ACCESSIBILITY_OVERLAY）不需要
 * “显示在其他应用上层”权限。窗口不可见、不可触摸、不可聚焦，
 * 对用户零感知。
 *
 * 双层冗余：前台服务挂应用层窗口、无障碍服务挂无障碍层窗口。
 * 两层必须分别记账 —— 若共用一个"已挂载"布尔量，第二层会被直接顶掉，
 * "双保险"就只剩一层（旧实现的缺陷）。
 */
object KeepAliveOverlay {

    /** 按 windowType 分别记录已挂载的窗口，两层互不顶替。 */
    private val views = mutableMapOf<Int, View>()

    @Synchronized
    fun acquire(context: Context, windowType: Int): Boolean {
        if (views.containsKey(windowType)) return true
        val windowManager = context.getSystemService(WindowManager::class.java) ?: return false
        val target = View(context)
        val layoutParams = WindowManager.LayoutParams().apply {
            type = windowType
            format = PixelFormat.TRANSLUCENT
            flags = WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
            gravity = Gravity.START or Gravity.TOP
            width = 1
            height = 1
            packageName = context.packageName
        }
        return try {
            windowManager.addView(target, layoutParams)
            views[windowType] = target
            true
        } catch (_: Exception) {
            false
        }
    }

    /** 只释放指定层；调用方传入自己挂载时用的 windowType。 */
    @Synchronized
    fun release(context: Context, windowType: Int) {
        val current = views.remove(windowType) ?: return
        val windowManager = context.getSystemService(WindowManager::class.java) ?: return
        runCatching { windowManager.removeView(current) }
    }

    val isAttached: Boolean
        get() = views.isNotEmpty()

    /** 无障碍层窗口类型：不需要悬浮窗权限。SDK 28 以下退回 TYPE_PHONE。 */
    @Suppress("DEPRECATION")
    fun accessibilityWindowType(): Int =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }

    /** 应用层窗口类型：需要 SYSTEM_ALERT_WINDOW 权限，由前台服务使用。 */
    const val APP_WINDOW_TYPE: Int = WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
}
