package io.smsrelay.collector

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.app.PendingIntent
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class SmsRelayForegroundService : Service() {
    private val commandPollExecutor = Executors.newSingleThreadScheduledExecutor { task ->
        Thread(task, "sms-relay-command-heartbeat").apply { isDaemon = true }
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        val notification = buildNotification()
        // 必须在任何其他逻辑之前完成 startForeground，且**必须包异常**：
        // Android 12+ 有"后台启动前台服务"限制，从磁贴/后台拉起时可能抛
        // ForegroundServiceStartNotAllowedException。旧代码未捕获，异常会从
        // onCreate 冒泡 → 服务进程直接崩溃 → SmsRelayRuntime.serviceActive 永远是
        // false → 磁贴"点了开不起来"。这里改为：捕获后停服务并给一条可点击通知。
        val foregroundReady = runCatching {
            if (Build.VERSION.SDK_INT >= 34) {
                // specialUse：常驻监听类服务的正确类型（对齐 GKD StatusService）。
                // dataSync 在 Android 15 有 6h/24h 预算，超时系统强停服务且后续启动全部失败。
                ServiceCompat.startForeground(
                    this,
                    NOTIFICATION_ID,
                    notification,
                    android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
                )
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
        }.onFailure { error ->
            Log.w(TAG, "前台服务启动被系统拒绝（后台启动限制）", error)
        }.isSuccess

        if (!foregroundReady) {
            SmsRelayRuntime.serviceActive = false
            notifyForegroundBlocked()
            stopSelf()
            return
        }

        SmsRelayRuntime.serviceActive = true
        attachAppOverlayIfPossible()
        // 指令下行复用既有 DeviceCommandPoller。前台服务存活时按 60 秒心跳领取，
        // 避免设备长时间没有新短信时只能等 WorkManager 的 15 分钟兜底；
        // DeviceCommandPoller 自带 30 秒防抖和重入保护，不会与短信上报冲突。
        commandPollExecutor.scheduleWithFixedDelay(
            { runCatching { DeviceCommandPoller.poll(this) } },
            COMMAND_POLL_INITIAL_DELAY_SECONDS,
            COMMAND_POLL_INTERVAL_SECONDS,
            TimeUnit.SECONDS,
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            // The immediate in-memory path is started directly by the SMS receiver.
            // A service-triggered upload must drain the durable queue as well, otherwise
            // rows left pending after a process/service restart can remain stranded.
            BackgroundRuntime.ACTION_UPLOAD_NOW -> SmsRelayUploadRunner.trigger(this)
            BackgroundRuntime.ACTION_KEEP_ALIVE -> {
                // 无障碍保活重申前台服务时补挂悬浮窗，
                // 覆盖系统窗口崩溃后窗口被回收的情况。
                attachAppOverlayIfPossible()
                Unit
            }
            else -> UploadScheduler.enqueue(this)
        }
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // 最近任务划掉：多数 ROM 直接杀进程而不走 START_STICKY 重启。
        // 这里立即重新拉起前台服务；若本次拉起也被杀，
        // 无障碍服务系统重连与周期看门狗会继续兜底。
        runCatching {
            BackgroundRuntime.start(this)
        }
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        commandPollExecutor.shutdownNow()
        SmsRelayRuntime.serviceActive = false
        KeepAliveOverlay.release(this, KeepAliveOverlay.APP_WINDOW_TYPE)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    /**
     * 前台服务附加 1x1 透明悬浮窗（GKD 同款双保险的第二层）。
     * SYSTEM_ALERT_WINDOW 权限缺失或被 ROM 回收时静默跳过——
     * 无障碍层的窗口才是主保活，这里只是尽力而为的冗余。
     */
    private fun attachAppOverlayIfPossible() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(this)) {
            return
        }
        KeepAliveOverlay.acquire(this, KeepAliveOverlay.APP_WINDOW_TYPE)
    }

    /**
     * 后台启动前台服务被系统拒绝时的兜底提示。
     *
     * 用户与通知交互本身是系统认可的"允许后台启动前台服务"豁免场景，
     * 点一下即可恢复到前台运行。没有这条兜底，磁贴开关会静默失效，
     * 用户只会看到"点了没反应"。
     */
    private fun notifyForegroundBlocked() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        // 用独立的高可见度渠道：常驻通知渠道是 IMPORTANCE_LOW，
        // 借它发告警用户可能根本看不到。
        val notification = NotificationCompat.Builder(this, ALERT_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
            .setContentTitle("Relay 转发未能恢复")
            .setContentText("系统限制了后台启动，点此打开应用即可恢复转发")
            .setContentIntent(
                PendingIntent.getActivity(
                    this,
                    2002,
                    Intent(this, MainActivity::class.java),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                ),
            )
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_ERROR)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        runCatching { manager.notify(BLOCKED_NOTIFICATION_ID, notification) }
    }

    private fun buildNotification(): Notification = NotificationCompat.Builder(this, CHANNEL_ID)
        .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
        .setContentTitle("Relay短信转发")
        .setContentText("后台等待短信并自动上报")
        .setContentIntent(
            PendingIntent.getActivity(
                this,
                2001,
                Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            ),
        )
        .setOngoing(true)
        .setCategory(NotificationCompat.CATEGORY_SERVICE)
        .setPriority(NotificationCompat.PRIORITY_LOW)
        .build()

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Relay后台服务", NotificationManager.IMPORTANCE_LOW).apply {
                description = "保持短信接收与上报服务运行"
                setShowBadge(false)
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(ALERT_CHANNEL_ID, "Relay 异常提醒", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "转发被系统限制等需要用户处理的提醒"
                setShowBadge(true)
            },
        )
    }

    private companion object {
        const val TAG = "RelayFgService"
        const val CHANNEL_ID = "relay_background"
        const val ALERT_CHANNEL_ID = "relay_alerts"
        const val NOTIFICATION_ID = 2001
        const val BLOCKED_NOTIFICATION_ID = 2003
        const val COMMAND_POLL_INITIAL_DELAY_SECONDS = 5L
        const val COMMAND_POLL_INTERVAL_SECONDS = 60L
    }
}
