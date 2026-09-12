package io.smsrelay.collector

import android.content.Context
import android.content.pm.PackageManager
import android.provider.Settings
import androidx.core.content.ContextCompat

/**
 * 无障碍服务的程序化开启/重启（对齐 GKD 的 WRITE_SECURE_SETTINGS 方案）。
 *
 * 前提：已通过 ADB 或 Shizuku 授予 WRITE_SECURE_SETTINGS 权限：
 *   adb shell pm grant io.smsrelay.collector android.permission.WRITE_SECURE_SETTINGS
 *
 * 有该权限后，应用可直接修改系统设置 Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES，
 * 实现：首次一键开启无障碍（无需手动进设置页）、无障碍被系统关闭/进程被杀后自动重启。
 * 无该权限时回退到手动引导（打开系统无障碍设置页）。
 */
object AccessibilityAutoToggle {

    /** 是否已持有 WRITE_SECURE_SETTINGS（系统级权限，运行时检查）。 */
    fun canWriteSecureSettings(context: Context): Boolean =
        ContextCompat.checkSelfPermission(
            context,
            android.Manifest.permission.WRITE_SECURE_SETTINGS,
        ) == PackageManager.PERMISSION_GRANTED

    /** 已启用的无障碍服务列表（系统设置）。 */
    private fun getEnabledServices(context: Context): String =
        Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ).orEmpty()

    private fun putEnabledServices(context: Context, value: String) {
        Settings.Secure.putString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            value,
        )
    }

    /** 开启本应用的无障碍服务（需 WRITE_SECURE_SETTINGS）。返回是否成功。 */
    fun enable(context: Context): Boolean {
        if (!canWriteSecureSettings(context)) return false
        return runCatching {
            val current = getEnabledServices(context).split(':')
                .map(String::trim).filter(String::isNotEmpty).toMutableSet()
            val component = "${context.packageName}/${AccessibilityKeepAlive.serviceClassName}"
            if (component !in current) {
                current.add(component)
                putEnabledServices(context, current.joinToString(":"))
            }
            Settings.Secure.putInt(
                context.contentResolver,
                Settings.Secure.ACCESSIBILITY_ENABLED,
                1,
            )
        }.isSuccess
    }

    /**
     * 重启无障碍服务（先移除再加入，强制系统重新绑定）。
     * 用于无障碍服务异常无响应时的自愈（对齐 GKD 的 fixA11yService）。
     */
    fun restart(context: Context): Boolean {
        if (!canWriteSecureSettings(context)) return false
        return runCatching {
            val component = "${context.packageName}/${AccessibilityKeepAlive.serviceClassName}"
            val current = getEnabledServices(context).split(':')
                .map(String::trim).filter(String::isNotEmpty).toMutableSet()
            if (component in current) {
                current.remove(component)
                putEnabledServices(context, current.joinToString(":"))
                Thread.sleep(REBIND_DELAY_MS)
            }
            current.add(component)
            putEnabledServices(context, current.joinToString(":"))
            Settings.Secure.putInt(
                context.contentResolver,
                Settings.Secure.ACCESSIBILITY_ENABLED,
                1,
            )
        }.isSuccess
    }

    private const val REBIND_DELAY_MS = 1000L
}
