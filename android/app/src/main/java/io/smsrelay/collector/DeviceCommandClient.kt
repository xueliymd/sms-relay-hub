package io.smsrelay.collector

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.provider.Telephony
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

data class PendingDeviceCommand(val id: String, val commandType: String)

/**
 * 远程控制下行通道客户端：轮询待执行指令 + 回传执行结果。
 * 复用上报链路的 HMAC 规范（timestamp\nnonce\tsha256(body)），
 * 轮询为 GET（空 body 签名），结果回传为 POST。
 */
class DeviceCommandClient {

    fun pendingCommands(config: SmsRelayConfig): List<PendingDeviceCommand> {
        val response = request(
            config = config,
            method = "GET",
            path = "/api/v1/devices/${config.deviceId}/commands/pending",
            body = "",
        )
        if (response.statusCode !in 200..299) return emptyList()
        val payload = runCatching { JSONObject(response.body) }.getOrNull() ?: return emptyList()
        val commands = payload.optJSONArray("commands") ?: return emptyList()
        return (0 until commands.length()).mapNotNull { index ->
            val item = commands.optJSONObject(index) ?: return@mapNotNull null
            val id = item.optString("id").takeIf { it.isNotBlank() } ?: return@mapNotNull null
            val type = item.optString("command_type").takeIf { it.isNotBlank() } ?: return@mapNotNull null
            PendingDeviceCommand(id, type)
        }
    }

    fun postResult(config: SmsRelayConfig, commandId: String, result: Map<String, Any?>): Int {
        val body = JSONObject().put("result", JSONObject(result)).toString()
        return request(
            config = config,
            method = "POST",
            path = "/api/v1/devices/${config.deviceId}/commands/$commandId/result",
            body = body,
        ).statusCode
    }

    private fun request(config: SmsRelayConfig, method: String, path: String, body: String): CommandHttpResponse {
        val timestamp = (System.currentTimeMillis() / 1000L).toString()
        val nonce = UUID.randomUUID().toString()
        val connection = (URL("${config.baseUrl.trimEnd('/')}$path").openConnection() as HttpURLConnection)
        return try {
            connection.requestMethod = method
            connection.connectTimeout = 5_000
            connection.readTimeout = 5_000
            connection.setRequestProperty("X-Device-Id", config.deviceId)
            connection.setRequestProperty("X-Timestamp", timestamp)
            connection.setRequestProperty("X-Nonce", nonce)
            connection.setRequestProperty("X-Signature", SmsRelaySigner.sign(config.hmacSecret, timestamp, nonce, body))
            if (method != "GET") {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            val text = if (status in 200..299) {
                connection.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
            } else {
                ""
            }
            CommandHttpResponse(status, text)
        } finally {
            connection.disconnect()
        }
    }
}

data class CommandHttpResponse(val statusCode: Int, val body: String)

/**
 * 指令执行器：按端侧授权开关决定是否真正执行，未授权时回传说明。
 * query_recent_sms 需要 READ_SMS 运行时权限，缺失时同样回传说明而不报错。
 */
object DeviceCommandExecutor {

    fun execute(context: Context, commandType: String, config: SmsRelayConfig): Map<String, Any?> {
        val settings = CommandSettingsStore(context)
        if (!settings.isAuthorized(commandType)) {
            return mapOf(
                "error" to "command_not_authorized",
                "message" to "设备端未开启该指令授权，请在采集端应用内开启后重试",
            )
        }
        return when (commandType) {
            CommandSettingsStore.COMMAND_QUERY_BATTERY -> queryBattery(context)
            CommandSettingsStore.COMMAND_QUERY_RECENT_SMS -> queryRecentSms(context)
            CommandSettingsStore.COMMAND_TRIGGER_UPLOAD -> triggerUpload(context)
            else -> mapOf(
                "error" to "unsupported_command",
                "message" to "当前采集端版本不支持该指令",
            )
        }
    }

    private fun queryBattery(context: Context): Map<String, Any?> {
        val manager = context.getSystemService(BatteryManager::class.java)
        val percent = runCatching {
            manager?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        }.getOrNull()?.takeIf { it in 0..100 }
        val status = runCatching {
            context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
                ?.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        }.getOrNull()
        val charging = when (status) {
            BatteryManager.BATTERY_STATUS_CHARGING, BatteryManager.BATTERY_STATUS_FULL -> true
            BatteryManager.BATTERY_STATUS_DISCHARGING, BatteryManager.BATTERY_STATUS_NOT_CHARGING -> false
            else -> null
        }
        return buildMap {
            put("battery_percent", percent ?: "unknown")
            put("charging", charging)
            put("collected_at", System.currentTimeMillis() / 1000L)
        }
    }

    private fun queryRecentSms(context: Context): Map<String, Any?> {
        val granted = context.checkSelfPermission(android.Manifest.permission.READ_SMS) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
        if (!granted) {
            return mapOf(
                "error" to "permission_missing",
                "message" to "采集端未授予读取短信权限，无法读取设备短信",
            )
        }
        val items = mutableListOf<Map<String, Any?>>()
        runCatching {
            // 不使用 sortOrder 里的 LIMIT 语法（个别厂商 provider 不支持），
            // 只按时间倒序取游标，循环内截断到 RECENT_SMS_LIMIT 条。
            context.contentResolver.query(
                Telephony.Sms.Inbox.CONTENT_URI,
                arrayOf(Telephony.Sms.ADDRESS, Telephony.Sms.BODY, Telephony.Sms.DATE),
                null,
                null,
                "${Telephony.Sms.DATE} DESC",
            )?.use { cursor ->
                while (cursor.moveToNext() && items.size < RECENT_SMS_LIMIT) {
                    items.add(
                        mapOf(
                            "sender" to maskSender(cursor.getString(0)),
                            "body" to cursor.getString(1)?.take(200),
                            "received_at" to cursor.getLong(2) / 1000L,
                        ),
                    )
                }
            }
        }
        return mapOf(
            "count" to items.size,
            "messages" to items,
        )
    }

    private fun triggerUpload(context: Context): Map<String, Any?> {
        SmsRelayUploadRunner.trigger(context)
        val pending = runCatching { SmsRelayRuntime.repository(context).pendingCount() }.getOrDefault(0)
        return mapOf(
            "queued" to pending,
            "triggered_at" to System.currentTimeMillis() / 1000L,
        )
    }

    /** 号码脱敏：保留后 4 位，其余用 * 代替；非数字（如 106 开头或英文发件人）原样返回。 */
    private fun maskSender(value: String?): String {
        val text = value.orEmpty().trim()
        if (text.isEmpty()) return "未知发件人"
        return if (text.length > 4) {
            text.take(text.length - 4).map { '*' }.joinToString("") + text.takeLast(4)
        } else {
            text
        }
    }

    private const val RECENT_SMS_LIMIT = 5
}

/**
 * 指令轮询入口：在已有上报周期（短信到达/后台看门狗）顺带拉取并执行指令。
 * 轮询本身失败静默，等下一轮；重入由 polling 标志挡住，
 * 避免 trigger_upload 指令触发上报又反过来触发轮询造成递归。
 */
object DeviceCommandPoller {
    private val polling = java.util.concurrent.atomic.AtomicBoolean(false)

    /** 两次轮询之间的最小间隔：短信密集时避免每条短信都多发一次 HTTP 请求。 */
    private const val MIN_POLL_INTERVAL_MS = 30_000L
    @Volatile private var lastPollAt = 0L

    fun poll(context: Context) {
        val appContext = context.applicationContext
        val config = SmsRelayRuntime.config(appContext) ?: return
        val now = System.currentTimeMillis()
        if (now - lastPollAt < MIN_POLL_INTERVAL_MS) return
        if (!polling.compareAndSet(false, true)) return
        lastPollAt = now
        Thread {
            try {
                val client = DeviceCommandClient()
                for (command in client.pendingCommands(config)) {
                    val result = DeviceCommandExecutor.execute(appContext, command.commandType, config)
                    client.postResult(config, command.id, result)
                }
            } catch (_: Exception) {
                // 轮询/回传失败不影响上报主链路，下一轮重试
            } finally {
                polling.set(false)
            }
        }.apply {
            name = "sms-relay-command-poll"
            isDaemon = true
            start()
        }
    }
}
