package io.smsrelay.collector

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.core.content.ContextCompat

object BackgroundRuntime {
    const val foregroundServiceType = "specialUse"
    const val ACTION_UPLOAD_NOW = "io.smsrelay.collector.action.UPLOAD_NOW"
    const val ACTION_KEEP_ALIVE = "io.smsrelay.collector.action.KEEP_ALIVE"

    fun needsNotificationPermission(sdkInt: Int): Boolean = sdkInt >= Build.VERSION_CODES.TIRAMISU

    fun start(context: Context) {
        startService(context, Intent(context, SmsRelayForegroundService::class.java))
    }

    /**
     * 停止前台服务：用户主动"暂停转发"时调用。
     * 显式 stopService 之后 START_STICKY 不会再把服务拉回（START_STICKY 只覆盖
     * "被系统杀死"的场景，不覆盖"应用自己停"）。
     */
    fun stop(context: Context) {
        context.stopService(Intent(context, SmsRelayForegroundService::class.java))
    }

    fun requestImmediateUpload(context: Context) {
        startService(
            context,
            Intent(context, SmsRelayForegroundService::class.java)
                .setAction(ACTION_UPLOAD_NOW),
        )
    }

    fun ensureKeepAlive(context: Context) {
        startService(
            context,
            Intent(context, SmsRelayForegroundService::class.java)
                .setAction(ACTION_KEEP_ALIVE),
        )
    }

    private fun startService(context: Context, intent: Intent) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            ContextCompat.startForegroundService(context, intent)
        } else {
            context.startService(intent)
        }
    }

    fun hasNotificationPermission(context: Context): Boolean =
        !needsNotificationPermission(Build.VERSION.SDK_INT) ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    fun isIgnoringBatteryOptimizations(context: Context): Boolean {
        val powerManager = context.getSystemService(PowerManager::class.java) ?: return false
        return powerManager.isIgnoringBatteryOptimizations(context.packageName)
    }

    /**
     * 打开系统"电池优化"设置页，由用户自己手动把本应用设为"不优化/不受限制"。
     *
     * 这是与 GKD 一致的保守做法（GKD 的 `ignoreBatteryOptimizations` 也是先判状态、
     * 再把用户引到系统页面）：只做"跳系统页面"的快捷键，不直接弹"是否忽略电池优化"
     * 的请求框 —— 后者在部分 ROM 上会解析到一个立即 finish 的活动，表现为
     * "点一下黑屏闪一下就结束"。
     *
     * 尝试顺序：
     *   ① 电池优化列表页（AOSP 标准；无需额外权限，各 ROM 普遍识别）
     *   ② 本应用专属的忽略请求页（部分 ROM 会落到本应用的省电策略页）
     *   ③ 应用详情页（最终兜底，用户可自行找到电池/省电入口）
     * 每一步都包异常并"拉不起来就换下一个"，避免抛
     * ActivityNotFoundException/SecurityException 直接把前台进程带崩。
     */
    fun openBatteryOptimizationSettings(context: Context) {
        val packageUri = Uri.parse("package:${context.packageName}")
        val intents = listOf(
            Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS),
            Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, packageUri),
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, packageUri),
        )
        startFirstResolvable(context, intents)
    }

    /**
     * 打开"省电 / 自启动"相关设置页，由用户自行把本应用设为"不受限制"。
     *
     * 原先是两个按钮（"后台运行不受限制" 与 "打开应用详情（自启动/省电）"），
     * 实际在多数 ROM 上落到同一处，已合并为本方法。尝试顺序：
     *   ① APP_BATTERY_USAGE_SETTINGS（本应用的耗电/后台限制页，多数 ROM 的"省电策略"）
     *   ② 应用详情页（一定存在；自启动/省电开关通常从这里进去）
     *   ③ 系统设置首页（最终兜底）
     * 不深链厂商自启动组件：部分 ROM 组件名不同或被禁止外部启动，
     * 硬拼 ComponentName 会黑屏/崩溃（GKD 也是保守做法 + 可选的机型表）。
     */
    fun openBackgroundRestrictionSettings(context: Context) {
        val packageUri = Uri.parse("package:${context.packageName}")
        val intents = buildList {
            add(Intent("android.settings.APP_BATTERY_USAGE_SETTINGS", packageUri))
            add(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, packageUri))
            add(Intent(Settings.ACTION_SETTINGS))
        }
        startFirstResolvable(context, intents)
    }

    /**
     * 依次尝试列表中的意图，直到有一个真正被成功拉起。
     *
     * 与之前"只取第一个可解析者"的写法不同：这里对每个候选都做
     * "可解析 + 包异常"双重保护，并且"拉不起来就继续试下一个"。
     * 旧写法把 startActivity 放在 runCatching 之外，一旦目标活动在
     * 本 ROM 上实际不可启动（SecurityException / ActivityNotFoundException），
     * 异常会直接冒泡并终止前台进程——对外表现就是"点一下闪一下就退出"。
     *
     * 这里**不做 resolveActivity 预判**：Android 11+ 的软件包可见性会让
     * 对系统设置页的 resolveActivity 查询返回 null，反而把可用的候选挡掉。
     * 直接 startActivity + 捕获异常更可靠：成功即返回，失败就试下一个。
     */
    private fun startFirstResolvable(context: Context, intents: List<Intent>) {
        for (intent in intents) {
            val launchable = intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (runCatching { context.startActivity(launchable) }.isSuccess) return
        }
    }

}
