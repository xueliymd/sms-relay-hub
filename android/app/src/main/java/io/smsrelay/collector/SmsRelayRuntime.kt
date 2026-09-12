package io.smsrelay.collector

import android.content.Context

/** Process-local state shared by the SMS receiver and the foreground service. */
object SmsRelayRuntime {
    private val lock = Any()
    @Volatile private var appContext: Context? = null
    @Volatile private var configLoaded = false
    @Volatile private var cachedConfig: SmsRelayConfig? = null
    @Volatile private var snapshotCache: SubscriptionSnapshotCache? = null
    @Volatile private var queueRepository: SmsRelayQueueRepository? = null

    /**
     * 前台服务当前是否真的在运行。由 [SmsRelayForegroundService] 的
     * onCreate/onDestroy 维护。进程被系统杀死时该标志随进程一起消失，
     * 因此磁贴重新绑定时读到 false —— 恰好是"服务确实没在跑"的正确答案。
     */
    @Volatile var serviceActive: Boolean = false

    fun initialize(context: Context) {
        val applicationContext = context.applicationContext
        if (appContext == null) {
            synchronized(lock) {
                if (appContext == null) {
                    appContext = applicationContext
                    SmsRelayDatabase.get(applicationContext)
                    queueRepository = SmsRelayQueueRepository(applicationContext)
                    snapshotCache = SubscriptionSnapshotCache { slotIndex, subscriptionId ->
                        SubscriptionSnapshotProvider(applicationContext).capture(slotIndex, subscriptionId)
                    }
                }
            }
        }
        loadConfig(applicationContext)
    }

    fun config(context: Context): SmsRelayConfig? {
        initialize(context)
        return cachedConfig
    }

    fun updateConfig(context: Context, config: SmsRelayConfig) {
        initialize(context)
        synchronized(lock) {
            cachedConfig = config
            configLoaded = true
        }
    }

    fun clearConfig() {
        synchronized(lock) {
            cachedConfig = null
            configLoaded = false
        }
    }

    fun captureSubscription(context: Context, slotIndexHint: Int?, subscriptionIdHint: Int?): SubscriptionSnapshot {
        initialize(context)
        return requireNotNull(snapshotCache).capture(slotIndexHint, subscriptionIdHint)
    }

    fun repository(context: Context): SmsRelayQueueRepository {
        initialize(context)
        return requireNotNull(queueRepository)
    }

    fun invalidateSubscriptionCache() {
        snapshotCache?.clear()
    }

    private fun loadConfig(context: Context) {
        if (configLoaded) return
        synchronized(lock) {
            if (!configLoaded) {
                cachedConfig = SmsRelayConfigStore(context).load()
                configLoaded = true
            }
        }
    }
}

class SubscriptionSnapshotCache(
    private val loader: (slotIndexHint: Int?, subscriptionIdHint: Int?) -> SubscriptionSnapshot,
) {
    private data class Key(val slotIndex: Int?, val subscriptionId: Int?)

    private val lock = Any()
    private val values = mutableMapOf<Key, SubscriptionSnapshot>()

    fun capture(slotIndexHint: Int?, subscriptionIdHint: Int?): SubscriptionSnapshot {
        if (subscriptionIdHint == null) {
            return loader(slotIndexHint, null)
        }
        val key = Key(slotIndexHint, subscriptionIdHint)
        synchronized(lock) {
            values[key]?.let { return it }
        }
        val loaded = loader(slotIndexHint, subscriptionIdHint)
        synchronized(lock) {
            return values.getOrPut(key) { loaded }
        }
    }

    fun clear() {
        synchronized(lock) { values.clear() }
    }
}
