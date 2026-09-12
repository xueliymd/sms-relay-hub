package io.smsrelay.manager

import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.ArrayAdapter
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import androidx.appcompat.app.AppCompatActivity
import io.smsrelay.manager.model.AdminDevice
import io.smsrelay.manager.model.AdminMessage
import io.smsrelay.manager.model.ConnectionState
import io.smsrelay.manager.model.DeviceCommand
import io.smsrelay.manager.model.PlatformProfile
import io.smsrelay.manager.model.ReleaseEntry
import io.smsrelay.manager.model.RuntimeInfo
import io.smsrelay.manager.model.SimProfile
import io.smsrelay.manager.model.commandTypeLabel
import io.smsrelay.manager.net.AdminClientException
import io.smsrelay.manager.net.AdminMessageSocket
import io.smsrelay.manager.net.ApiClientInfo
import io.smsrelay.manager.net.BaseUrl
import io.smsrelay.manager.net.RelayAdminClient
import io.smsrelay.manager.profile.PlatformProfileStore
import io.smsrelay.manager.state.MessageTimeline
import io.smsrelay.manager.state.ReleasePolicy
import io.smsrelay.manager.ui.ApiScreen
import io.smsrelay.manager.ui.DashboardScreen
import io.smsrelay.manager.ui.DeviceScreen
import io.smsrelay.manager.ui.MessageScreen
import io.smsrelay.manager.ui.PlatformScreen
import io.smsrelay.manager.ui.actionButton
import io.smsrelay.manager.ui.addMatch
import io.smsrelay.manager.ui.bodyText
import io.smsrelay.manager.ui.sectionTitle
import io.smsrelay.manager.ui.vertical
import okhttp3.OkHttpClient
import java.util.UUID
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {
    private val worker: ExecutorService = Executors.newCachedThreadPool()
    private val httpClient = OkHttpClient()
    private val timeline = MessageTimeline(capacity = 5000)
    private lateinit var profileStore: PlatformProfileStore
    private var platformScreen: PlatformScreen? = null
    private var dashboard: DashboardScreen? = null
    private var currentProfileId: String? = null
    private var currentBaseUrl: BaseUrl? = null
    private var currentClient: RelayAdminClient? = null
    private var currentToken: String? = null
    private var runtime: RuntimeInfo? = null
    private var devices: List<AdminDevice> = emptyList()
    private var simProfiles: Map<String, List<SimProfile>> = emptyMap()
    private var releases: Map<String, List<ReleaseEntry>> = emptyMap()
    private var deviceCommands: Map<String, List<DeviceCommand>> = emptyMap()
    private var selectedDeviceId: String? = null
    private var selectedSimSlot: Int? = null
    private var messageScreen: MessageScreen? = null
    private var messageSocket: AdminMessageSocket? = null
    private var messageSocketSession: UUID? = null
    private var messageHistoryOffset = 500
    private var messageHistoryHasMore = true
    private var messageHistoryLoading = false
    private var connectionState = ConnectionState.DISCONNECTED
    private var messageDiagnostics = ""
    private var selectedTab = Tab.MESSAGES
    private var apiClients: List<ApiClientInfo> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        profileStore = PlatformProfileStore(this)
        showLogin()
    }

    override fun onDestroy() {
        Log.i(LOG_TAG, "main activity destroyed")
        stopMessageSocket()
        worker.shutdownNow()
        httpClient.dispatcher.executorService.shutdown()
        httpClient.connectionPool.evictAll()
        super.onDestroy()
    }

    private fun showLogin(status: String? = null) {
        stopMessageSocket()
        selectedTab = Tab.PLATFORM
        dashboard = null
        messageScreen = null
        val screen = PlatformScreen(
            context = this,
            onProfileSelected = ::selectProfile,
            onSave = ::saveProfile,
            onDelete = ::deleteProfile,
            onTest = ::testConnection,
            onLogin = ::login,
        )
        platformScreen = screen
        setContentView(screen.view)
        refreshProfiles()
        status?.let { screen.statusText.text = it }
    }

    private fun refreshProfiles(preferredId: String? = currentProfileId) {
        val profiles = profileStore.list()
        val selected = profiles.firstOrNull { it.id == preferredId } ?: profileStore.selected() ?: profiles.firstOrNull()
        currentProfileId = selected?.id
        platformScreen?.setProfiles(profiles, selected?.id)
        if (selected != null) {
            showProfile(selected)
        }
    }

    private fun selectProfile(profile: PlatformProfile) {
        currentProfileId = profile.id
        runCatching { profileStore.select(profile.id) }
        showProfile(profile)
    }

    private fun showProfile(profile: PlatformProfile) {
        platformScreen?.showProfile(profile, profileStore.password(profile.id))
    }

    private fun profileFromInputs(): Pair<PlatformProfile, BaseUrl> {
        val screen = requireNotNull(platformScreen)
        val baseUrl = BaseUrl.parse(screen.addressInput.text.toString())
        val profile = PlatformProfile(
            id = currentProfileId ?: UUID.randomUUID().toString(),
            label = screen.labelInput.text.toString().trim().ifEmpty { baseUrl.value },
            baseUrl = baseUrl.value,
            username = screen.usernameInput.text.toString().trim(),
            rememberPassword = screen.rememberPassword.isChecked,
        )
        require(profile.username.isNotEmpty()) { "管理员用户名不能为空" }
        return profile to baseUrl
    }

    private fun saveProfile() {
        val screen = platformScreen ?: return
        runCatching {
            val (profile, _) = profileFromInputs()
            profileStore.save(profile, screen.passwordInput.text.toString(), null)
            profileStore.select(profile.id)
            currentProfileId = profile.id
        }.onSuccess {
            refreshProfiles()
            screen.statusText.text = "平台配置已保存"
        }.onFailure { screen.statusText.text = errorText(it) }
    }

    private fun deleteProfile() {
        val profileId = currentProfileId ?: run {
            platformScreen?.statusText?.text = "当前没有可删除的平台"
            return
        }
        profileStore.delete(profileId)
        currentProfileId = null
        currentBaseUrl = null
        currentClient = null
        currentToken = null
        val screen = platformScreen ?: return
        screen.labelInput.text.clear()
        screen.addressInput.text.clear()
        screen.usernameInput.text.clear()
        screen.passwordInput.text.clear()
        screen.rememberPassword.isChecked = false
        refreshProfiles(null)
        screen.statusText.text = "平台配置已删除"
    }

    private fun testConnection() {
        val screen = platformScreen ?: return
        val baseUrl = runCatching { profileFromInputs().second }.getOrElse {
            screen.statusText.text = errorText(it)
            return
        }
        screen.statusText.text = "正在测试连接…"
        worker.execute {
            val result = runCatching { RelayAdminClient(baseUrl, httpClient).health() }
            runOnUiThread {
                screen.statusText.text = result.fold(
                    onSuccess = { if (it) "平台连接正常" else "平台健康检查未通过" },
                    onFailure = ::errorText,
                )
            }
        }
    }

    private fun login() {
        val screen = platformScreen ?: return
        val input = runCatching { profileFromInputs() }.getOrElse {
            screen.statusText.text = errorText(it)
            return
        }
        val profile = input.first
        val baseUrl = input.second
        val password = screen.passwordInput.text.toString()
        val storedToken = profileStore.sessionToken(profile.id)
        if (password.isBlank() && storedToken.isNullOrBlank()) {
            screen.statusText.text = "请输入管理员密码"
            return
        }
        screen.statusText.text = "正在登录并读取平台状态…"
        worker.execute {
            val result = runCatching {
                val client = RelayAdminClient(baseUrl, httpClient)
                val token = if (password.isNotBlank()) client.login(profile.username, password).token else requireNotNull(storedToken)
                val snapshot = loadSnapshot(client, token)
                LoginResult(client, token, snapshot)
            }
            runOnUiThread {
                result.onSuccess { loginResult ->
                    profileStore.save(profile, password.takeIf { it.isNotBlank() }, loginResult.token)
                    profileStore.select(profile.id)
                    currentProfileId = profile.id
                    currentBaseUrl = baseUrl
                    currentClient = loginResult.client
                    currentToken = loginResult.token
                    applySnapshot(loginResult.snapshot)
                    showDashboard()
                }.onFailure { error ->
                    if (error is AdminClientException.Unauthorized) profileStore.clearSession(profile.id)
                    screen.statusText.text = errorText(error)
                }
            }
        }
    }

    private fun loadSnapshot(client: RelayAdminClient, token: String): Snapshot {
        val runtimeInfo = client.runtime(token)
        val deviceList = client.devices(token)
        val releaseMap = listOf("android", "manager", "root").associateWith { platform ->
            runCatching { client.releaseHistory(platform) }.getOrDefault(emptyList())
        }
        return Snapshot(runtimeInfo, deviceList, releaseMap)
    }

    private fun applySnapshot(snapshot: Snapshot) {
        runtime = snapshot.runtime
        devices = snapshot.devices
        releases = snapshot.releases
        deviceCommands = emptyMap()
        selectedDeviceId = null
        selectedSimSlot = null
        resetMessageHistory()
        connectionState = ConnectionState.DISCONNECTED
    }

    private fun showDashboard() {
        val baseUrl = currentBaseUrl ?: return
        val screen = DashboardScreen(
            context = this,
            onPlatform = { showTab(Tab.PLATFORM) },
            onMessages = { showTab(Tab.MESSAGES) },
            onDevices = { showTab(Tab.DEVICES) },
            onApi = { showTab(Tab.API) },
            onLogout = ::logout,
        )
        dashboard = screen
        messageScreen = MessageScreen(
            context = this,
            onDevice = ::selectDevice,
            onSim = ::selectSim,
            onLoadMore = ::loadMoreMessages,
            onCopy = ::copyText,
            onToggleRead = ::toggleMessageRead,
            onDelete = ::deleteMessage,
            onBulkDelete = ::bulkDeleteMessages,
            onRetention = ::runRetentionCleanup,
        )
        screen.warningText.text = if (baseUrl.secure) "" else "警告：当前使用 HTTP，账号密码和短信内容未被传输加密"
        screen.connectionText.text = connectionLabel()
        setContentView(screen.view)
        showTab(Tab.MESSAGES)
        startMessageSocket()
        loadSimProfiles()
        loadApiClients()
        loadDeviceCommands(devices.map { it.id })
    }

    private fun showTab(tab: Tab) {
        selectedTab = tab
        val content = when (tab) {
            Tab.PLATFORM -> platformOverview()
            Tab.MESSAGES -> requireNotNull(messageScreen).apply {
                update(
                    devices = devices,
                    selectedDeviceId = selectedDeviceId,
                    selectedSimSlot = selectedSimSlot,
                messages = timeline.items(),
                status = connectionLabel(),
                    canLoadMore = messageHistoryHasMore,
                    loadingMore = messageHistoryLoading,
                )
            }.view
            Tab.DEVICES -> DeviceScreen(
                context = this,
                onCreateDevice = ::showCreateDeviceDialog,
                onRotate = ::rotateDevice,
                onRevoke = ::revokeDevice,
                onRestore = ::restoreDevice,
                onPurge = ::purgeDevice,
                onSimEdit = ::showSimEditDialog,
                onSimDelete = ::deleteSimMapping,
                commands = deviceCommands,
                onSendCommand = ::sendDeviceCommand,
                onRefreshCommands = { device -> loadDeviceCommands(listOf(device.id)) },
            ).render(devices, simProfiles)
            Tab.API -> ApiScreen(this).render(
                requireNotNull(currentBaseUrl),
                ::copyText,
                apiClients,
                onCreateToken = ::createApiToken,
                onRefreshTokens = ::loadApiClients,
            )
        }
        dashboard?.show(content)
        dashboard?.connectionText?.text = connectionLabel()
    }

    private fun platformOverview(): View {
        val root = vertical(this, 8)
        root.addView(sectionTitle(this, "平台运行状态"))
        val runtimeInfo = runtime
        if (runtimeInfo == null) {
            root.addView(bodyText(this, "平台状态尚未加载"))
        } else {
            root.addView(bodyText(this, "平台版本：" + runtimeInfo.platformVersion))
            val publicAddress = runtimeInfo.publicBaseUrl.ifBlank { currentBaseUrl?.value ?: "未配置" }
            root.addView(bodyText(this, "公开地址：" + publicAddress))
            root.addView(bodyText(this, "数据库：" + runtimeInfo.databaseBackend + " · " + runtimeInfo.databaseFingerprint))
            root.addView(bodyText(this, "端口：HTTP " + runtimeInfo.httpPort + " / HTTPS " + runtimeInfo.httpsPort + " / App " + runtimeInfo.appPort))
            root.addView(bodyText(this, "HTTPS：" + if (runtimeInfo.httpsEnabled) "已启用" else "未启用"))
            runtimeInfo.services.forEach { (name, state) -> root.addView(bodyText(this, "服务 " + name + "：" + state)) }
            root.addView(bodyText(this, "服务器时间：" + runtimeInfo.serverTime))
        }
        root.addMatch(actionButton(this, "手动检查更新并刷新") { refreshDashboardData() })
        root.addMatch(actionButton(this, "修改管理员账号") { showAdminAccountDialog() })
        val managerHistory = releases["manager"].orEmpty()
        root.addView(
            bodyText(
                this,
                if (ReleasePolicy.updateAvailable(managerHistory, BuildConfig.VERSION_CODE)) {
                    "管理端有新版本：" + requireNotNull(ReleasePolicy.latest(managerHistory)).versionName
                } else {
                    "管理端当前已是最新版 " + BuildConfig.VERSION_NAME
                },
            ),
        )
        root.addView(sectionTitle(this, "版本历史"))
        listOf(
            "android" to "采集端",
            "manager" to "管理端",
            "root" to "Root 模块",
        ).forEach { (platform, label) ->
            val history = releases[platform].orEmpty()
            if (history.isEmpty()) {
                root.addView(bodyText(this, label + "：暂无版本记录"))
            } else {
                val latest = history.first()
                root.addView(bodyText(this, label + "：" + latest.versionName + " · " + latest.publishedAt))
                root.addView(bodyText(this, "SHA256 " + latest.sha256))
                root.addMatch(actionButton(this, "下载 " + label + " " + latest.versionName) { openRelease(latest) })
            }
        }
        return ScrollView(this).apply { addView(root) }
    }

    private fun loadSimProfiles() {
        val client = currentClient ?: return
        val token = currentToken ?: return
        val snapshotDevices = devices
        worker.execute {
            val loaded = snapshotDevices.associate { device ->
                device.id to runCatching { client.simProfiles(token, device.id) }.getOrDefault(emptyList())
            }
            runOnUiThread {
                if (client !== currentClient || token != currentToken) return@runOnUiThread
                simProfiles = loaded
                if (selectedTab == Tab.DEVICES) showTab(Tab.DEVICES)
            }
        }
    }

    private fun refreshDashboardData() {
        val client = currentClient ?: return
        val token = currentToken ?: return
        dashboard?.connectionText?.text = "正在刷新平台状态和版本历史…"
        worker.execute {
            val result = runCatching { loadSnapshot(client, token) }
            runOnUiThread {
                if (client !== currentClient || token != currentToken) return@runOnUiThread
                result.onSuccess { snapshot ->
                    val previousDeviceId = selectedDeviceId
                    runtime = snapshot.runtime
                    devices = snapshot.devices
                    releases = snapshot.releases
                    selectedDeviceId = previousDeviceId?.takeIf { id ->
                        devices.any { it.id == id && it.active }
                    }
                    if (selectedDeviceId != previousDeviceId) {
                        selectedSimSlot = null
                        resetMessageHistory()
                        restartMessageSocket()
                    }
                    loadSimProfiles()
                    showTab(selectedTab)
                }.onFailure { error ->
                    if (error is AdminClientException.Unauthorized) {
                        handleExpiredSession()
                    } else {
                        dashboard?.connectionText?.text = errorText(error)
                    }
                }
            }
        }
    }

    private fun openRelease(release: ReleaseEntry) {
        val baseUrl = currentBaseUrl ?: return
        runCatching {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(baseUrl.http(release.downloadUrl))))
        }.onFailure { dashboard?.connectionText?.text = "无法打开下载地址" }
    }

    private fun selectDevice(deviceId: String?) {
        if (deviceId == selectedDeviceId) return
        selectedDeviceId = deviceId
        selectedSimSlot = null
        resetMessageHistory()
        restartMessageSocket()
        showTab(Tab.MESSAGES)
    }

    private fun selectSim(simSlot: Int?) {
        if (simSlot == selectedSimSlot) return
        selectedSimSlot = simSlot
        resetMessageHistory()
        restartMessageSocket()
        showTab(Tab.MESSAGES)
    }

    private fun loadMoreMessages() {
        if (messageHistoryLoading) return
        val client = currentClient ?: return
        val token = currentToken ?: return
        val deviceId = selectedDeviceId
        val simSlot = selectedSimSlot
        val offset = messageHistoryOffset
        messageHistoryLoading = true
        showTab(Tab.MESSAGES)
        worker.execute {
            val result = runCatching {
                client.messages(token, deviceId, simSlot, cursor = null, offset = offset)
            }
            runOnUiThread {
                if (client !== currentClient || token != currentToken ||
                    deviceId != selectedDeviceId || simSlot != selectedSimSlot
                ) {
                    messageHistoryLoading = false
                    return@runOnUiThread
                }
                messageHistoryLoading = false
                result.onSuccess { messages ->
                    if (messages.isEmpty()) {
                        messageHistoryHasMore = false
                    } else {
                        timeline.merge(messages)
                        messageHistoryOffset += messages.size
                        if (messages.size < 500) messageHistoryHasMore = false
                    }
                }.onFailure { dashboard?.connectionText?.text = errorText(it) }
                showTab(Tab.MESSAGES)
            }
        }
    }

    private fun startMessageSocket() {
        val client = currentClient ?: return
        val baseUrl = currentBaseUrl ?: return
        val token = currentToken ?: return
        val deviceId = selectedDeviceId
        val simSlot = selectedSimSlot
        val socketSession = UUID.randomUUID()
        messageDiagnostics = ""
        Log.i(LOG_TAG, "start message socket: session=$socketSession, device=${deviceId ?: "all"}, sim=${simSlot ?: "all"}")
        val socket = AdminMessageSocket(
            httpClient = httpClient,
            baseUrl = baseUrl,
            token = token,
            deviceId = deviceId,
            simSlot = simSlot,
            cursorProvider = timeline::cursor,
            recover = { cursor ->
                runCatching { client.messages(token, deviceId, simSlot, cursor) }
                    .onSuccess { messageDiagnostics = "初始补拉 ${it.size} 条" }
                    .onFailure { messageDiagnostics = "补拉失败：" + it.javaClass.simpleName + ": " + it.message }
                    .getOrThrow()
            },
            listener = object : AdminMessageSocket.Listener {
                override fun onState(state: ConnectionState) {
                    runOnUiThread {
                        Log.i(LOG_TAG, "message socket state: state=$state, session=$socketSession, current=$messageSocketSession")
                        if (socketIsCurrent(socketSession, deviceId, simSlot)) {
                            connectionState = state
                            dashboard?.connectionText?.text = connectionLabel()
                            if (selectedTab == Tab.MESSAGES) showTab(Tab.MESSAGES)
                            if (state == ConnectionState.AUTHENTICATION_FAILED) handleExpiredSession()
                        }
                    }
                }

                override fun onMessages(messages: List<io.smsrelay.manager.model.AdminMessage>) {
                    timeline.merge(messages)
                    runOnUiThread {
                        if (socketIsCurrent(socketSession, deviceId, simSlot) && selectedTab == Tab.MESSAGES) {
                            showTab(Tab.MESSAGES)
                        }
                    }
                }
            },
        )
        messageSocket = socket
        messageSocketSession = socketSession
        socket.start()
    }

    private fun socketIsCurrent(session: UUID?, deviceId: String?, simSlot: Int?): Boolean =
        session != null && session == messageSocketSession && selectedDeviceId == deviceId && selectedSimSlot == simSlot

    private fun restartMessageSocket() {
        Log.i(LOG_TAG, "restart message socket")
        stopMessageSocket()
        startMessageSocket()
    }

    private fun stopMessageSocket() {
        Log.i(LOG_TAG, "stop message socket: session=$messageSocketSession")
        messageSocket?.stop()
        messageSocket = null
        messageSocketSession = null
        connectionState = ConnectionState.DISCONNECTED
        dashboard?.connectionText?.text = connectionLabel()
    }

    private fun resetMessageHistory() {
        timeline.clear()
        messageHistoryOffset = 500
        messageHistoryHasMore = true
        messageHistoryLoading = false
    }

    // ---------- 写操作：统一经二次确认 + 后台线程执行 + 结果提示 ----------

    /** 二次确认对话框；destructive=true 时提示为危险操作。 */
    private fun confirm(title: String, message: String, onConfirm: () -> Unit) {
        AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage(message)
            .setNegativeButton("取消", null)
            .setPositiveButton("确认执行") { _, _ -> onConfirm() }
            .show()
    }

    /** 后台线程执行管理端写操作，回到主线程交付结果。 */
    private fun <T> runAdminOp(action: (RelayAdminClient, String) -> T, onSuccess: (T) -> Unit) {
        val client = currentClient ?: return
        val token = currentToken ?: return
        dashboard?.connectionText?.text = "正在执行…"
        worker.execute {
            val result = runCatching { action(client, token) }
            runOnUiThread {
                if (client !== currentClient || token != currentToken) return@runOnUiThread
                result.onSuccess(onSuccess).onFailure { error ->
                    if (error is AdminClientException.Unauthorized) {
                        handleExpiredSession()
                    } else {
                        dashboard?.connectionText?.text = errorText(error)
                    }
                }
            }
        }
    }

    /** 操作成功后刷新设备与 SIM 数据并留在设备页。 */
    private fun refreshDevicesAfterOp(notice: String) {
        val client = currentClient ?: return
        val token = currentToken ?: return
        worker.execute {
            val newDevices = runCatching { client.devices(token) }.getOrDefault(devices)
            val newProfiles = newDevices.associate { device ->
                device.id to runCatching { client.simProfiles(token, device.id) }.getOrDefault(emptyList())
            }
            runOnUiThread {
                if (client !== currentClient || token != currentToken) return@runOnUiThread
                devices = newDevices
                simProfiles = newProfiles
                dashboard?.connectionText?.text = notice
                showTab(Tab.DEVICES)
            }
        }
    }

    /** 一次性凭据展示对话框（创建/轮换/恢复后只显示一次）。 */
    private fun showCredentialDialog(title: String, credential: String) {
        AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage("请立即复制保存，此凭据只显示一次：\n\n$credential")
            .setPositiveButton("复制凭据") { _, _ -> copyText(credential) }
            .setNegativeButton("关闭", null)
            .show()
    }

    private fun showCreateDeviceDialog() {
        val types = listOf(
            "android_app" to "Android 应用",
            "root_module" to "Android Root",
            "ios_shortcut" to "iPhone 快捷指令",
            "ve_tweak" to "iOS Ve",
            "air724_mqtt" to "合宙 724 MQTT 短信上报",
            "air724" to "合宙 724 取码",
        )
        val nameInput = EditText(this).apply { hint = "设备名称，例如：客厅备用机" }
        val typeSpinner = Spinner(this).apply {
            adapter = ArrayAdapter(
                this@MainActivity,
                android.R.layout.simple_spinner_dropdown_item,
                types.map { it.second },
            )
        }
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
            addView(nameInput)
            addView(typeSpinner)
        }
        AlertDialog.Builder(this)
            .setTitle("创建设备")
            .setView(container)
            .setNegativeButton("取消", null)
            .setPositiveButton("创建") { _, _ ->
                val name = nameInput.text.toString().trim().ifEmpty { "未命名设备" }
                val clientType = types[typeSpinner.selectedItemPosition].first
                runAdminOp({ client, token -> client.createDevice(token, name, clientType) }) { created ->
                    refreshDevicesAfterOp("设备已创建")
                    showCredentialDialog("设备凭据（仅显示一次）", created.credential)
                    loadDeviceCommands(listOf(created.deviceId))
                }
            }
            .show()
    }

    private fun rotateDevice(device: AdminDevice) {
        confirm("轮换凭据", "设备「${device.name}」的旧凭据将立即失效，需要重新配置采集端。确认轮换？") {
            runAdminOp({ client, token -> client.rotateDeviceCredential(token, device.id) }) { secret ->
                refreshDevicesAfterOp("凭据已轮换")
                showCredentialDialog("新凭据（仅显示一次）", secret)
            }
        }
    }

    private fun revokeDevice(device: AdminDevice) {
        confirm("撤销设备", "设备「${device.name}」将被停用（可恢复），停止接收其上报。确认撤销？") {
            runAdminOp({ client, token -> client.revokeDevice(token, device.id) }) {
                refreshDevicesAfterOp("设备已撤销")
            }
        }
    }

    private fun restoreDevice(device: AdminDevice) {
        confirm("恢复设备", "恢复设备「${device.name}」并生成新凭据。确认恢复？") {
            runAdminOp({ client, token -> client.restoreDevice(token, device.id) }) { secret ->
                refreshDevicesAfterOp("设备已恢复")
                showCredentialDialog("新凭据（仅显示一次）", secret)
            }
        }
    }

    private fun purgeDevice(device: AdminDevice) {
        confirm("永久删除", "设备「${device.name}」及其全部消息数据将被永久删除，不可恢复！确认永久删除？") {
            runAdminOp({ client, token -> client.purgeDevice(token, device.id) }) {
                refreshDevicesAfterOp("设备已永久删除")
            }
        }
    }

    private fun showSimEditDialog(device: AdminDevice, sim: SimProfile) {
        val input = EditText(this).apply {
            // 不预填脱敏号码（含 * 掩码），否则原样提交会把掩码写进平台
            hint = "输入完整手机号，例如 13800138000"
        }
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
            addView(input)
        }
        AlertDialog.Builder(this)
            .setTitle("修改 SIM${sim.slotIndex + 1} 号码映射")
            .setMessage("设备「${device.name}」当前映射：${sim.phoneNumberMasked ?: "未识别"}。输入新的完整号码。")
            .setView(container)
            .setNegativeButton("取消", null)
            .setPositiveButton("保存") { _, _ ->
                val phone = input.text.toString().trim()
                if (!phone.matches(Regex("^\\+?[0-9\\s().-]{5,20}$"))) {
                    dashboard?.connectionText?.text = "手机号格式无效，请输入完整号码"
                    return@setPositiveButton
                }
                runAdminOp({ client, token -> client.upsertSimProfile(token, device.id, sim.slotIndex, phone) }) {
                    refreshDevicesAfterOp("SIM 号码映射已保存")
                }
            }
            .show()
    }

    private fun deleteSimMapping(device: AdminDevice, sim: SimProfile) {
        confirm("删除映射", "删除设备「${device.name}」SIM${sim.slotIndex + 1} 的手机号映射，之后该卡槽号码将显示为未识别。确认删除？") {
            runAdminOp({ client, token -> client.deleteSimProfile(token, device.id, sim.slotIndex) }) {
                refreshDevicesAfterOp("SIM 号码映射已删除")
            }
        }
    }

    private fun toggleMessageRead(message: AdminMessage) {
        val markRead = message.readAt == null
        runAdminOp({ client, token -> client.setMessageRead(token, message.id, markRead) }) { readAt ->
            timeline.merge(listOf(message.copy(readAt = readAt)))
            dashboard?.connectionText?.text = if (markRead) "已标为已读" else "已标为未读"
            showTab(Tab.MESSAGES)
        }
    }

    private fun deleteMessage(message: AdminMessage) {
        confirm("删除短信", "删除这条来自 ${message.sender} 的短信？此操作不可恢复。") {
            runAdminOp({ client, token -> client.deleteMessage(token, message.id) }) {
                timeline.remove(message.id)
                dashboard?.connectionText?.text = "短信已删除"
                showTab(Tab.MESSAGES)
            }
        }
    }

    private fun bulkDeleteMessages() {
        val loaded = timeline.items()
        if (loaded.isEmpty()) {
            dashboard?.connectionText?.text = "当前列表没有可删除的短信"
            return
        }
        confirm("批量删除", "将删除当前列表已加载的全部 ${loaded.size} 条短信，此操作不可恢复！确认批量删除？") {
            runAdminOp({ client, token ->
                // 后端单次批量删除上限 500 条（RelayAdminClient.bulkDeleteMessages 内 require 校验）。
                // 时间线最多可累积 5000 条，此前直接把整个列表发过去会在 >500 时抛异常、
                // 整次操作一条都删不掉。这里按 500 分片串行提交并累加实际删除条数。
                val ids = loaded.map { it.id }
                ids.chunked(BULK_DELETE_CHUNK).sumOf { chunk ->
                    client.bulkDeleteMessages(token, chunk)
                }
            }) { deleted ->
                timeline.removeAll(loaded.map { it.id })
                dashboard?.connectionText?.text = "已批量删除 $deleted 条短信"
                showTab(Tab.MESSAGES)
            }
        }
    }

    private fun runRetentionCleanup() {
        confirm("数据保留清理", "按平台保留策略清理过期短信（正文脱敏），此操作不可恢复。确认执行？") {
            runAdminOp({ client, token -> client.runRetention(token) }) { redacted ->
                dashboard?.connectionText?.text = "数据保留清理完成，处理 $redacted 条消息"
            }
        }
    }

    /**
     * 下发远程控制指令。结果不在同一次请求里返回 —— 设备要等下一次轮询（采集端最短 30 秒）
     * 才会取走并执行，所以这里只确认"已下发"，再由 loadDeviceCommands 刷新回传结果。
     */
    private fun sendDeviceCommand(device: AdminDevice, commandType: String) {
        val label = commandTypeLabel(commandType)
        confirm("下发指令", "向设备「${device.name}」下发「$label」？设备需在线运行采集端，并在采集端内开启了该指令授权。") {
            runAdminOp({ client, token -> client.createDeviceCommand(token, device.id, commandType) }) { command ->
                deviceCommands = deviceCommands + (device.id to (listOf(command) + deviceCommands[device.id].orEmpty()))
                dashboard?.connectionText?.text = "「$label」已下发，等待设备下一次轮询执行"
                showTab(Tab.DEVICES)
                loadDeviceCommands(listOf(device.id))
            }
        }
    }

    /** 拉取指定设备的指令记录（含已回传结果）；失败静默保留旧数据，不打断页面。 */
    private fun loadDeviceCommands(deviceIds: List<String>) {
        if (deviceIds.isEmpty()) return
        val client = currentClient ?: return
        val token = currentToken ?: return
        worker.execute {
            val loaded = deviceIds.associateWith { id ->
                runCatching { client.deviceCommands(token, id) }.getOrDefault(emptyList())
            }
            runOnUiThread {
                if (client !== currentClient || token != currentToken) return@runOnUiThread
                deviceCommands = deviceCommands + loaded
                if (selectedTab == Tab.DEVICES) showTab(Tab.DEVICES)
            }
        }
    }

    private fun loadApiClients() {
        val client = currentClient ?: return
        val token = currentToken ?: return
        worker.execute {
            val result = runCatching { client.listApiClients(token) }
            runOnUiThread {
                if (client !== currentClient || token != currentToken) return@runOnUiThread
                result.onSuccess { clients ->
                    apiClients = clients
                    if (selectedTab == Tab.API) showTab(Tab.API)
                }.onFailure { error ->
                    if (error is AdminClientException.Unauthorized) handleExpiredSession()
                }
            }
        }
    }

    private fun createApiToken() {
        confirm("新建脚本令牌", "将创建一个新的脚本 API 令牌（srh_api_ 开头），用于外部脚本取码。确认创建？") {
            runAdminOp({ client, token -> client.createApiClient(token) }) { newToken ->
                loadApiClients()
                showCredentialDialog("脚本 API 令牌（仅显示一次）", newToken)
            }
        }
    }

    private fun showAdminAccountDialog() {
        val currentInput = EditText(this).apply {
            hint = "当前密码（必填）"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val usernameInput = EditText(this).apply { hint = "新用户名（留空则不修改）" }
        val passwordInput = EditText(this).apply {
            hint = "新密码（留空则不修改）"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
            addView(currentInput)
            addView(usernameInput)
            addView(passwordInput)
        }
        AlertDialog.Builder(this)
            .setTitle("修改管理员账号")
            .setMessage("修改成功后需要重新登录。")
            .setView(container)
            .setNegativeButton("取消", null)
            .setPositiveButton("确认修改") { _, _ ->
                val currentPassword = currentInput.text.toString()
                if (currentPassword.isBlank()) {
                    dashboard?.connectionText?.text = "请输入当前密码"
                    return@setPositiveButton
                }
                val newUsername = usernameInput.text.toString().trim().takeIf { it.isNotEmpty() }
                val newPassword = passwordInput.text.toString().takeIf { it.isNotEmpty() }
                if (newUsername == null && newPassword == null) {
                    dashboard?.connectionText?.text = "新用户名和新密码至少填写一项"
                    return@setPositiveButton
                }
                runAdminOp({ client, token ->
                    client.updateAdminAccount(token, currentPassword, newUsername, newPassword)
                }) {
                    logout()
                    platformScreen?.statusText?.text = "管理员账号已修改，请用新凭据重新登录"
                }
            }
            .show()
    }

    private fun handleExpiredSession() {
        currentProfileId?.let(profileStore::clearSession)
        currentToken = null
        currentClient = null
        showLogin("登录会话已失效，请重新输入管理员密码")
    }

    private fun logout() {
        stopMessageSocket()
        currentProfileId?.let(profileStore::clearSession)
        currentToken = null
        currentClient = null
        runtime = null
        devices = emptyList()
        simProfiles = emptyMap()
        releases = emptyMap()
        timeline.clear()
        showLogin("已退出登录")
    }

    private fun connectionLabel(): String = when (connectionState) {
        ConnectionState.DISCONNECTED -> "实时连接未启动"
        ConnectionState.CONNECTING -> "实时连接中…"
        ConnectionState.CONNECTED -> if (selectedDeviceId == null) "实时短信已连接（全部设备）" else "实时短信已连接"
        ConnectionState.RECOVERING -> "正在同步最近短信…"
        ConnectionState.AUTHENTICATION_FAILED -> "管理员会话失效"
        ConnectionState.ERROR -> "实时连接异常，正在重连"
    }.let { status ->
        val diagnostics = messageDiagnostics.takeIf { it.isNotBlank() }
        if (diagnostics == null) status else status + "\n" + diagnostics
    }

    private fun copyText(value: String) {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("Relay Hub", value))
    }

    private fun errorText(error: Throwable): String = when (error) {
        is AdminClientException.Unauthorized -> "管理员用户名、密码或会话无效\n请求地址：" + error.url
        is AdminClientException.NetworkFailure -> "无法连接平台，请检查地址、端口、网络和 HTTPS 证书\n请求地址：" + error.url + (error.reason?.let { "\n系统原因：$it" }.orEmpty())
        is AdminClientException.HttpFailure -> "平台请求失败，HTTP " + error.statusCode + "\n请求地址：" + error.url
        is AdminClientException.InvalidResponse -> "平台返回了无法识别的数据\n请求地址：" + error.url
        is IllegalArgumentException -> error.message ?: "输入内容无效"
        else -> "操作失败：" + (error.message ?: error.javaClass.simpleName)
    }

    private companion object {
        const val LOG_TAG = "RelayManager"

        /** 后端 /admin/messages/bulk-delete 单次上限，必须与 RelayAdminClient 的 require 保持一致。 */
        const val BULK_DELETE_CHUNK = 500
    }

    private enum class Tab { PLATFORM, MESSAGES, DEVICES, API }

    private data class Snapshot(
        val runtime: RuntimeInfo,
        val devices: List<AdminDevice>,
        val releases: Map<String, List<ReleaseEntry>>,
    )

    private data class LoginResult(
        val client: RelayAdminClient,
        val token: String,
        val snapshot: Snapshot,
    )
}
