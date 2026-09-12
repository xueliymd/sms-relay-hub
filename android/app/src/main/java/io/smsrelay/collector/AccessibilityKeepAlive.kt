package io.smsrelay.collector

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings

object AccessibilityKeepAlive {
    const val serviceClassName = "io.smsrelay.collector.RelayKeepAliveAccessibilityService"

    fun isEnabled(context: Context): Boolean {
        val enabledServices = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        )
        return AccessibilityServiceState.containsService(
            enabledServices = enabledServices,
            packageName = context.packageName,
            serviceClassName = serviceClassName,
        )
    }

    fun openSettings(context: Context) {
        if (context !is Activity) {
            openAccessibilitySettings(context)
            return
        }
        AlertDialog.Builder(context)
            .setTitle("开启无障碍保活")
            .setMessage(
                "如果系统设置页提示“受限制的设置”，请先完成：\n\n" +
                    "1. 打开应用信息\n" +
                    "2. 点击右上角 ⋮\n" +
                    "3. 选择“允许受限制的设置”\n" +
                    "4. 返回后再次开启 Relay 保活辅助服务\n\n" +
                    "这是 Android 的系统安全限制，应用无法代替你开启。",
            )
            .setNegativeButton("打开应用信息") { _, _ -> openAppDetails(context) }
            .setPositiveButton("打开无障碍设置") { _, _ -> openAccessibilitySettings(context) }
            .show()
    }

    fun openAppDetails(context: Context) {
        context.startActivity(
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(Uri.parse("package:" + context.packageName))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }

    private fun openAccessibilitySettings(context: Context) {
        context.startActivity(
            Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}
