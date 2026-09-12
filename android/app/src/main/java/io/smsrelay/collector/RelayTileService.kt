package io.smsrelay.collector

import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import android.util.Log

/**
 * 通知栏快捷磁贴：转发开关 + 保活的"复活甲"。
 *
 * 磁贴常驻下拉通知栏，即使进程被杀、无障碍被回收，入口依然存在。
 * 点击磁贴 = 开/关切换（对齐 GKD 的 GkdTileService.onTileClick）；
 * 长按磁贴 = 打开本应用（由 AndroidManifest 里 MainActivity 的
 * QS_TILE_PREFERENCES intent-filter 承接，否则系统默认跳"应用详情"）。
 */
class RelayTileService : TileService() {

    override fun onTileAdded() {
        super.onTileAdded()
        syncTile()
    }

    override fun onStartListening() {
        super.onStartListening()
        // 自愈：开关是"开"但服务没在跑（被系统杀掉、上次启动被拦），
        // 展开通知栏时顺手补拉一次。best-effort —— 失败只记日志，
        // 绝不让异常冒泡（磁贴回调里抛异常会让整个磁贴失灵）。
        if (RelaySwitch.isEnabled(this) &&
            !SmsRelayRuntime.serviceActive &&
            SmsRelayRuntime.config(this) != null
        ) {
            runCatching {
                BackgroundRuntime.start(this)
                KeepAliveWatchdog.enqueuePeriodicWorker(this)
            }.onFailure { Log.w(TAG, "磁贴自愈启动失败", it) }
        }
        syncTile()
    }

    override fun onStopListening() {
        super.onStopListening()
        syncTile()
    }

    /**
     * 点击 = 开/关切换。
     *
     * **判据必须是持久开关（用户意图），不能是运行态。**
     * 旧实现用 `RelaySwitch && SmsRelayRuntime.serviceActive` 判断，而
     * `startForegroundService` 是异步的：点"开"的瞬间 `serviceActive` 还是 false，
     * 于是 `syncTile()` 仍显示"已暂停"；用户以为没生效再点一下，这一次 `serviceActive`
     * 已经变 true，反而把它关掉了 —— 对外表现就是"点开以后再也开不起来"。
     * 现在只翻转持久开关，开关本身永远不会卡死。
     *
     * 整段包 `runCatching`：Android 12+ 的后台启动限制可能让
     * `startForegroundService` 抛异常，不能让它把磁贴打崩。
     */
    override fun onClick() {
        super.onClick()
        runCatching {
            if (SmsRelayRuntime.config(this) == null) return@runCatching
            val enable = !RelaySwitch.isEnabled(this)
            RelaySwitch.setEnabled(this, enable)
            if (enable) {
                BackgroundRuntime.start(this)
                KeepAliveWatchdog.enqueuePeriodicWorker(this)
            } else {
                BackgroundRuntime.stop(this)
                KeepAliveWatchdog.cancel(this)
            }
        }.onFailure { Log.w(TAG, "磁贴切换失败", it) }
        syncTile()
    }

    /**
     * 磁贴状态 = 用户开关（开关型磁贴的语义就是这个）。
     *
     * 标签额外区分"服务是否真的在跑"，避免过去那种"显示运行中其实已死"的误导：
     * 开关开但服务没跑时显示"已开启"（而不是"运行中"），点按会把它关掉。
     */
    private fun syncTile() {
        val configured = SmsRelayRuntime.config(this) != null
        val enabled = RelaySwitch.isEnabled(this)
        val running = enabled && SmsRelayRuntime.serviceActive
        qsTile?.let { tile ->
            tile.state = if (configured && enabled) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
            tile.label = when {
                !configured -> "Relay转发未配置"
                !enabled -> "Relay转发已暂停"
                running -> "Relay转发运行中"
                else -> "Relay转发已开启"
            }
            tile.updateTile()
        }
    }

    private companion object {
        const val TAG = "RelayTile"
    }
}
