package io.smsrelay.manager.net

import io.smsrelay.manager.model.AdminDevice
import io.smsrelay.manager.model.AdminMessage
import io.smsrelay.manager.model.AdminSession
import io.smsrelay.manager.model.DeviceCommand
import io.smsrelay.manager.model.ReleaseEntry
import io.smsrelay.manager.model.RuntimeInfo
import io.smsrelay.manager.model.SimProfile
import io.smsrelay.manager.state.MessageCursor
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException

sealed class AdminClientException(message: String) : RuntimeException(message) {
    class Unauthorized(val url: String) : AdminClientException("administrator authentication failed: $url")
    class InvalidResponse(val url: String) : AdminClientException("platform returned an invalid response: $url")
    class HttpFailure(val statusCode: Int, val url: String) : AdminClientException("platform request failed: HTTP $statusCode $url")
    class NetworkFailure(val url: String, val reason: String?) : AdminClientException("platform network request failed: $url${reason?.let { " ($it)" }.orEmpty()}")
}

class RelayAdminClient(
    private val baseUrl: BaseUrl,
    private val httpClient: OkHttpClient = OkHttpClient(),
) {
    fun health(): Boolean {
        val request = Request.Builder().url(baseUrl.http("/api/v1/health")).get().build()
        return executeObject(request).optString("status") == "ok"
    }

    fun login(username: String, password: String): AdminSession {
        val body = JSONObject().put("username", username).put("password", password).toString()
        val request = Request.Builder()
            .url(baseUrl.http("/api/v1/admin/login"))
            .post(body.toRequestBody(JSON_MEDIA_TYPE))
            .build()
        val json = executeObject(request)
        return AdminSession(
            token = json.requiredString("token"),
            username = json.requiredString("username"),
            expiresAt = json.requiredString("expires_at"),
        )
    }

    fun runtime(token: String): RuntimeInfo {
        val json = executeObject(adminRequest("/api/v1/admin/runtime", token))
        val servicesJson = json.requiredObject("services")
        val services = servicesJson.keys().asSequence().associateWith { servicesJson.getString(it) }
        return RuntimeInfo(
            databaseBackend = json.requiredString("database_backend"),
            databaseFingerprint = json.requiredString("database_fingerprint"),
            platformVersion = json.requiredString("platform_version"),
            publicBaseUrl = json.optString("public_base_url").trim(),
            relaySite = json.optString("relay_site"),
            httpPort = json.getInt("http_port"),
            httpsPort = json.getInt("https_port"),
            appPort = json.getInt("app_port"),
            httpsEnabled = json.getBoolean("https_enabled"),
            services = services,
            serverTime = json.requiredString("server_time"),
        )
    }

    fun devices(token: String): List<AdminDevice> = executeArray(adminRequest("/api/v1/admin/devices", token)).objects().map { json ->
        AdminDevice(
            id = json.requiredString("id"),
            name = json.requiredString("name"),
            clientType = json.requiredString("client_type"),
            active = json.getBoolean("active"),
            online = json.optBoolean("online", false),
            lastSeenAt = json.nullableString("last_seen_at"),
            clientInstanceId = json.nullableString("client_instance_id"),
            clientVersion = json.nullableString("client_version"),
            clientMode = json.nullableString("client_mode"),
            credentialReuseWarning = json.optBoolean("credential_reuse_warning", false),
            duplicateCollectionWarning = json.optBoolean("duplicate_collection_warning", false),
        )
    }

    fun simProfiles(token: String, deviceId: String): List<SimProfile> = executeArray(
        adminRequest("/api/v1/admin/devices/" + deviceId + "/sim-profiles", token),
    ).objects().map { json ->
        SimProfile(
            deviceId = json.requiredString("device_id"),
            slotIndex = json.getInt("slot_index"),
            subscriptionId = json.nullableInt("subscription_id"),
            phoneNumberMasked = json.nullableString("phone_number_masked"),
            carrierName = json.nullableString("carrier_name"),
            source = json.requiredString("source"),
            updatedAt = json.requiredString("updated_at"),
        )
    }

    fun messages(
        token: String,
        deviceId: String?,
        simSlot: Int?,
        cursor: MessageCursor?,
        limit: Int = 500,
        offset: Int = 0,
    ): List<AdminMessage> {
        val url = baseUrl.http("/api/v1/admin/messages").toHttpUrl().newBuilder()
            .addQueryParameter("limit", limit.toString())
            .apply {
                if (deviceId != null) addQueryParameter("device_id", deviceId)
                if (simSlot != null) addQueryParameter("sim_slot", simSlot.toString())
                if (cursor != null) {
                    addQueryParameter("created_after", cursor.createdAt)
                    addQueryParameter("after_id", cursor.id)
                }
                if (offset > 0) addQueryParameter("offset", offset.toString())
            }
            .build()
        val request = Request.Builder().url(url).header(ADMIN_HEADER, token).get().build()
        return executeArray(request).objects().map(RelayJson::message)
    }

    fun releaseHistory(platform: String): List<ReleaseEntry> {
        require(platform in setOf("android", "manager", "root", "deploy")) { "unsupported release platform" }
        val request = Request.Builder()
            .url(baseUrl.http("/api/v1/releases/" + platform + "/history?channel=stable"))
            .get()
            .build()
        return executeObject(request).requiredArray("releases").objects().map { json ->
            ReleaseEntry(
                platform = json.requiredString("platform"),
                channel = json.requiredString("channel"),
                versionCode = json.getInt("version_code"),
                versionName = json.requiredString("version_name"),
                publishedAt = json.requiredString("published_at"),
                notes = json.optString("notes"),
                sizeBytes = json.optLong("size_bytes", 0),
                sha256 = json.optString("sha256"),
                downloadUrl = json.requiredString("download_url"),
            )
        }
    }

    // ---------- 写操作（网页端已有，管理端补齐；后端均要求 X-Admin-Token） ----------

    /** 创建设备，返回设备 ID 与一次性凭据明文。auth_kind 由 client_type 契约推导。 */
    fun createDevice(token: String, name: String, clientType: String): CreatedDevice {
        val authKind = DEVICE_AUTH_KINDS[clientType]
            ?: throw IllegalArgumentException("不支持的设备类型：$clientType")
        val body = JSONObject()
            .put("name", name)
            .put("client_type", clientType)
            .put("auth_kind", authKind)
            .toString()
        val json = executeObject(adminWriteRequest("/api/v1/admin/devices", token, "POST", body))
        return CreatedDevice(
            deviceId = json.requiredObject("device").requiredString("id"),
            credential = json.requiredObject("credential").requiredString("secret"),
        )
    }

    /** 轮换设备凭据，返回新凭据明文（旧凭据立即失效）。 */
    fun rotateDeviceCredential(token: String, deviceId: String): String =
        executeObject(adminWriteRequest("/api/v1/admin/devices/$deviceId/rotate-credential", token, "POST", "{}"))
            .requiredObject("credential").requiredString("secret")

    /** 恢复已撤销设备，返回新凭据明文。 */
    fun restoreDevice(token: String, deviceId: String): String =
        executeObject(adminWriteRequest("/api/v1/admin/devices/$deviceId/restore", token, "POST", "{}"))
            .requiredObject("credential").requiredString("secret")

    /** 撤销设备（软删除，可恢复）。 */
    fun revokeDevice(token: String, deviceId: String) =
        executeNoContent(adminWriteRequest("/api/v1/admin/devices/$deviceId", token, "DELETE", null))

    /** 永久删除已撤销设备及其全部数据，不可恢复。 */
    fun purgeDevice(token: String, deviceId: String) =
        executeNoContent(adminWriteRequest("/api/v1/admin/devices/$deviceId/purge", token, "DELETE", null))

    /** 保存 SIM 手机号映射（source 固定 manual，与网页端一致）。 */
    fun upsertSimProfile(token: String, deviceId: String, slot: Int, phoneNumber: String) {
        val body = JSONObject()
            .put("phone_number", phoneNumber)
            .put("source", "manual")
            .toString()
        executeObject(adminWriteRequest("/api/v1/admin/devices/$deviceId/sim-profiles/$slot", token, "PUT", body))
    }

    /** 删除 SIM 手机号映射。 */
    fun deleteSimProfile(token: String, deviceId: String, slot: Int) =
        executeNoContent(adminWriteRequest("/api/v1/admin/devices/$deviceId/sim-profiles/$slot", token, "DELETE", null))

    /** 标记消息已读/未读，返回最新的 read_at（未读为 null）。 */
    fun setMessageRead(token: String, messageId: String, read: Boolean): String? =
        executeObject(
            adminWriteRequest(
                "/api/v1/admin/messages/$messageId/" + if (read) "read" else "unread",
                token,
                "POST",
                "{}",
            ),
        ).nullableString("read_at")

    /** 删除单条消息。 */
    fun deleteMessage(token: String, messageId: String) =
        executeNoContent(adminWriteRequest("/api/v1/admin/messages/$messageId", token, "DELETE", null))

    /** 批量删除消息，返回实际删除条数。 */
    fun bulkDeleteMessages(token: String, messageIds: List<String>): Int {
        require(messageIds.isNotEmpty() && messageIds.size <= 500) { "批量删除需要 1~500 条消息" }
        val body = JSONObject().put("message_ids", JSONArray(messageIds)).toString()
        return executeObject(adminWriteRequest("/api/v1/admin/messages/bulk-delete", token, "POST", body))
            .getInt("deleted")
    }

    /** 执行数据保留清理，返回被清理（脱敏）的消息条数。 */
    fun runRetention(token: String): Int =
        executeObject(adminWriteRequest("/api/v1/admin/retention/run", token, "POST", "{}"))
            .getInt("redacted_messages")

    /** 修改管理员账号；返回新的会话（用户名/密码改动后旧令牌失效，需重新登录）。 */
    fun updateAdminAccount(token: String, currentPassword: String, username: String?, password: String?): AdminSession {
        val body = JSONObject().put("current_password", currentPassword)
        username?.takeIf { it.isNotBlank() }?.let { body.put("username", it) }
        password?.takeIf { it.isNotBlank() }?.let { body.put("password", it) }
        val json = executeObject(adminWriteRequest("/api/v1/admin/account", token, "PUT", body.toString()))
        return AdminSession(
            token = json.requiredString("token"),
            username = json.requiredString("username"),
            expiresAt = json.requiredString("expires_at"),
        )
    }

    /** 创建脚本 API 令牌，返回明文令牌。 */
    fun createApiClient(token: String): String =
        executeObject(adminWriteRequest("/api/v1/admin/api-clients", token, "POST", "{}"))
            .requiredString("token")

    // ---------- 远程控制 v1（与后端 /admin/devices/{id}/commands 对齐） ----------

    /** 列出设备最近指令（含设备回传结果，后端解密后返回）。 */
    fun deviceCommands(token: String, deviceId: String, limit: Int = 20): List<DeviceCommand> {
        val url = baseUrl.http("/api/v1/admin/devices/$deviceId/commands").toHttpUrl().newBuilder()
            .addQueryParameter("limit", limit.toString())
            .build()
        val request = Request.Builder().url(url).header(ADMIN_HEADER, token).get().build()
        return executeArray(request).objects().map(RelayJson::deviceCommand)
    }

    /** 下发指令；设备在下一次轮询（采集端最短 30 秒间隔）时取走并执行。 */
    fun createDeviceCommand(token: String, deviceId: String, commandType: String): DeviceCommand {
        require(commandType in DEVICE_COMMAND_TYPES) { "不支持的指令类型：$commandType" }
        val body = JSONObject().put("command_type", commandType).toString()
        val json = executeObject(
            adminWriteRequest("/api/v1/admin/devices/$deviceId/commands", token, "POST", body),
        )
        return RelayJson.deviceCommand(json)
    }

    /** 列出脚本 API 令牌（token 仅在服务端加密可解密时返回明文，否则为 null）。 */
    fun listApiClients(token: String): List<ApiClientInfo> =
        executeObject(adminRequest("/api/v1/admin/api-clients", token))
            .requiredArray("clients").objects().map { json ->
                ApiClientInfo(
                    id = json.requiredString("id"),
                    token = json.nullableString("token"),
                    active = json.optBoolean("active", true),
                    createdAt = json.requiredString("created_at"),
                )
            }

    private fun adminWriteRequest(path: String, token: String, method: String, jsonBody: String?): Request =
        Request.Builder()
            .url(baseUrl.http(path))
            .header(ADMIN_HEADER, token)
            .method(method, jsonBody?.toRequestBody(JSON_MEDIA_TYPE))
            .build()

    private fun executeNoContent(request: Request) {
        val response = try {
            httpClient.newCall(request).execute()
        } catch (error: IOException) {
            throw AdminClientException.NetworkFailure(request.url.toString(), error.message)
        }
        response.use {
            val url = request.url.toString()
            if (it.code == 401 || it.code == 403) throw AdminClientException.Unauthorized(url)
            if (!it.isSuccessful) throw AdminClientException.HttpFailure(it.code, url)
        }
    }

    private fun adminRequest(path: String, token: String): Request = Request.Builder()
        .url(baseUrl.http(path))
        .header(ADMIN_HEADER, token)
        .get()
        .build()

    private fun executeObject(request: Request): JSONObject = parse(request) { JSONObject(it) }
    private fun executeArray(request: Request): JSONArray = parse(request) { JSONArray(it) }

    private fun <T> parse(request: Request, parser: (String) -> T): T {
        val response = try {
            httpClient.newCall(request).execute()
        } catch (error: IOException) {
            throw AdminClientException.NetworkFailure(request.url.toString(), error.message)
        }
        response.use {
            val url = request.url.toString()
            if (it.code == 401 || it.code == 403) throw AdminClientException.Unauthorized(url)
            if (!it.isSuccessful) throw AdminClientException.HttpFailure(it.code, url)
            val body = it.body?.string() ?: throw AdminClientException.InvalidResponse(url)
            return try {
                parser(body)
            } catch (_: JSONException) {
                throw AdminClientException.InvalidResponse(url)
            }
        }
    }

    private companion object {
        const val ADMIN_HEADER = "X-Admin-Token"
        val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()

        /** 与后端 SUPPORTED_DEVICE_CONTRACTS 对齐：client_type → auth_kind。 */
        val DEVICE_AUTH_KINDS = mapOf(
            "android_app" to "hmac",
            "root_module" to "hmac",
            "ios_shortcut" to "bearer",
            "ve_tweak" to "hmac",
            "air724_mqtt" to "hmac",
            "air724" to "script_api",
        )

        /** 与后端 store.DEVICE_COMMAND_TYPES 对齐：超出白名单后端会返回 422。 */
        val DEVICE_COMMAND_TYPES = setOf("query_battery", "query_recent_sms", "trigger_upload")
    }
}

data class CreatedDevice(val deviceId: String, val credential: String)

data class ApiClientInfo(
    val id: String,
    val token: String?,
    val active: Boolean,
    val createdAt: String,
)

internal object RelayJson {
    fun message(json: JSONObject): AdminMessage {
        val receivedAt = json.requiredString("received_at")
        return AdminMessage(
            id = json.requiredString("id"),
            deviceId = json.requiredString("device_id"),
            receivedAt = receivedAt,
            createdAt = json.nullableString("created_at") ?: receivedAt,
            sender = json.nullableString("sender")?.takeIf { it.isNotBlank() } ?: "未知发件人",
            body = json.nullableString("body"),
            source = json.requiredString("source"),
            simSlot = json.nullableInt("sim_slot"),
            subscriptionId = json.nullableInt("subscription_id"),
            receivingPhoneMasked = json.nullableString("receiving_phone_masked"),
            receivingIdentityStatus = json.optString("receiving_identity_status", "unresolved"),
            otpCode = json.nullableString("otp_code"),
            messageDate = json.nullableString("message_date"),
            readAt = json.nullableString("read_at"),
        )
    }

    fun deviceCommand(json: JSONObject): DeviceCommand {
        // 后端未回传结果时 result 为 null；失败结果也是对象（形如 {"error":..,"message":..}）。
        val result = if (json.has("result") && !json.isNull("result")) json.optJSONObject("result") else null
        return DeviceCommand(
            id = json.requiredString("id"),
            deviceId = json.requiredString("device_id"),
            commandType = json.requiredString("command_type"),
            status = json.optString("status").ifBlank { "pending" },
            resultSummary = result?.let(::summarizeCommandResult),
            resultJson = result?.toString(),
            createdAt = json.nullableString("created_at") ?: "",
            sentAt = json.nullableString("sent_at"),
            completedAt = json.nullableString("completed_at"),
        )
    }

    /**
     * 把设备回传结果压成一行可读摘要。
     * 字段名与采集端 DeviceCommandExecutor 的三种回传结构一一对应：
     * query_battery{battery_percent,charging}、query_recent_sms{count,messages}、
     * trigger_upload{queued}；设备侧未授权/无权限时统一回传 error+message。
     */
    private fun summarizeCommandResult(result: JSONObject): String {
        val error = result.optString("error").takeIf { it.isNotBlank() }
        if (error != null) {
            val message = result.optString("message").takeIf { it.isNotBlank() }
            return "设备返回失败：" + (message ?: error)
        }
        return when {
            result.has("battery_percent") -> {
                val percent = result.opt("battery_percent")?.toString().orEmpty().ifBlank { "未知" }
                val charging = when {
                    !result.has("charging") || result.isNull("charging") -> "充电状态未知"
                    result.optBoolean("charging") -> "充电中"
                    else -> "未充电"
                }
                "电量 $percent% · $charging"
            }
            result.has("messages") -> "读取到最近短信 " + result.optInt("count") + " 条"
            result.has("queued") -> "已触发上报，本地队列剩余 " + result.optInt("queued") + " 条"
            else -> result.toString()
        }
    }
}

private fun JSONObject.requiredString(key: String): String = getString(key).takeIf { it.isNotBlank() }
    ?: throw JSONException(key + " is required")

private fun JSONObject.requiredObject(key: String): JSONObject = getJSONObject(key)
private fun JSONObject.requiredArray(key: String): JSONArray = getJSONArray(key)
private fun JSONObject.nullableString(key: String): String? = if (!has(key) || isNull(key)) null else getString(key)
private fun JSONObject.nullableInt(key: String): Int? = if (!has(key) || isNull(key)) null else getInt(key)
private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
