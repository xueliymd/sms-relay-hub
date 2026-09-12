package io.smsrelay.manager.net

import io.smsrelay.manager.model.AdminMessage
import io.smsrelay.manager.model.ConnectionState
import io.smsrelay.manager.state.MessageCursor
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

object AdminSocketProtocol {
    fun auth(token: String): String = JSONObject().put("type", "auth").put("token", token).toString()

    fun subscribe(deviceId: String?, simSlot: Int?): String = JSONObject()
        .put("type", "subscribe")
        .apply {
            if (deviceId != null) put("device_id", deviceId)
            if (simSlot != null) put("sim_slot", simSlot)
        }
        .toString()

    fun ping(): String = JSONObject().put("type", "ping").toString()
}

class AdminMessageSocket(
    private val httpClient: OkHttpClient,
    private val baseUrl: BaseUrl,
    private val token: String,
    private val deviceId: String?,
    private val simSlot: Int?,
    private val cursorProvider: () -> MessageCursor?,
    private val recover: (MessageCursor?) -> List<AdminMessage>,
    private val listener: Listener,
) {
    interface Listener {
        fun onState(state: ConnectionState)
        fun onMessages(messages: List<AdminMessage>)
    }

    private val scheduler = Executors.newSingleThreadScheduledExecutor()
    private val delaysSeconds = longArrayOf(1, 2, 5, 10, 30)
    private var socket: WebSocket? = null
    private var retry: ScheduledFuture<*>? = null
    private var heartbeat: ScheduledFuture<*>? = null
    private var attempt = 0
    @Volatile private var running = false
    @Volatile private var authenticationFailed = false
    @Volatile private var terminalFailure = false

    fun start() {
        if (running) return
        running = true
        scheduler.execute(::recoverAndConnect)
    }

    fun stop() {
        running = false
        retry?.cancel(true)
        heartbeat?.cancel(true)
        socket?.close(1000, "client stop")
        socket = null
        scheduler.shutdownNow()
        listener.onState(ConnectionState.DISCONNECTED)
    }

    private fun recoverAndConnect() {
        if (!running || authenticationFailed || terminalFailure) return
        listener.onState(ConnectionState.RECOVERING)
        try {
            val recovered = recover(cursorProvider())
            Log.i(LOG_TAG, "message recovery completed: count=${recovered.size}, hasCursor=${cursorProvider() != null}")
            recovered.takeIf { it.isNotEmpty() }?.let(listener::onMessages)
        } catch (_: AdminClientException.Unauthorized) {
            Log.w(LOG_TAG, "message recovery unauthorized")
            authenticationFailed = true
            listener.onState(ConnectionState.AUTHENTICATION_FAILED)
            return
        } catch (error: Throwable) {
            Log.w(LOG_TAG, "message recovery failed", error)
            listener.onState(ConnectionState.ERROR)
        }
        if (!running) return
        listener.onState(ConnectionState.CONNECTING)
        val request = Request.Builder().url(baseUrl.webSocket("/api/v1/ws/admin/messages")).build()
        socket = httpClient.newWebSocket(request, SocketListener())
    }

    private fun scheduleReconnect() {
        if (!running || authenticationFailed || terminalFailure || retry?.isDone == false) return
        val delay = delaysSeconds[minOf(attempt, delaysSeconds.lastIndex)]
        attempt += 1
        retry = scheduler.schedule(::recoverAndConnect, delay, TimeUnit.SECONDS)
    }

    private fun startHeartbeat(webSocket: WebSocket) {
        heartbeat?.cancel(true)
        heartbeat = scheduler.scheduleAtFixedRate(
            {
                if (running && socket === webSocket) webSocket.send(AdminSocketProtocol.ping())
            },
            60,
            60,
            TimeUnit.SECONDS,
        )
    }

    private fun cancelHeartbeat() {
        heartbeat?.cancel(true)
        heartbeat = null
    }

    private inner class SocketListener : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            Log.i(LOG_TAG, "admin message websocket opened")
            webSocket.send(AdminSocketProtocol.auth(token))
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val payload = runCatching { JSONObject(text) }.getOrNull() ?: return
            when (payload.optString("type")) {
                "auth_ok" -> webSocket.send(AdminSocketProtocol.subscribe(deviceId, simSlot))
                "subscribed" -> {
                    Log.i(LOG_TAG, "admin message websocket subscribed")
                    attempt = 0
                    startHeartbeat(webSocket)
                    listener.onState(ConnectionState.CONNECTED)
                }
                "message" -> runCatching { RelayJson.message(payload) }.getOrNull()?.let {
                    listener.onMessages(listOf(it))
                }
                "error" -> if (payload.optString("code") == "admin_auth_required") {
                    authenticationFailed = true
                    cancelHeartbeat()
                    listener.onState(ConnectionState.AUTHENTICATION_FAILED)
                    webSocket.close(1008, "authentication failed")
                } else if (payload.optString("code") in setOf("device_not_active", "invalid_auth", "invalid_subscription")) {
                    terminalFailure = true
                    cancelHeartbeat()
                    listener.onState(ConnectionState.ERROR)
                    webSocket.close(1008, "terminal protocol failure")
                }
            }
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            Log.i(LOG_TAG, "admin message websocket closed: code=$code, reason=$reason")
            cancelHeartbeat()
            scheduleReconnect()
        }

        override fun onFailure(webSocket: WebSocket, error: Throwable, response: Response?) {
            Log.w(LOG_TAG, "admin message websocket failed: httpCode=${response?.code}", error)
            cancelHeartbeat()
            listener.onState(ConnectionState.ERROR)
            scheduleReconnect()
        }
    }

    private companion object {
        const val LOG_TAG = "RelayAdminSocket"
    }
}
