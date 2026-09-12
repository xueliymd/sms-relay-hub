package io.smsrelay.collector

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.google.zxing.integration.android.IntentIntegrator
import com.google.zxing.integration.android.IntentResult

class MainActivity : Activity() {
    private lateinit var baseUrlInput: EditText
    private lateinit var deviceIdInput: EditText
    private lateinit var secretInput: EditText
    private lateinit var permissionStatus: TextView
    private lateinit var optionalPermissionStatus: TextView
    private lateinit var accessibilityStatus: TextView
    private lateinit var uploadStatus: TextView
    private lateinit var updateStatus: TextView
    private lateinit var installUpdateButton: Button
    private var relaySwitchButton: Button? = null
    private val configStore by lazy { SmsRelayConfigStore(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildContent())
        val config = SmsRelayRuntime.config(this)
        config?.let {
            baseUrlInput.setText(it.baseUrl)
            deviceIdInput.setText(it.deviceId)
            // 用户已暂停转发时不要在打开应用时偷偷恢复，状态由开关显式控制。
            if (RelaySwitch.isEnabled(this)) BackgroundRuntime.start(this)
        }
        refreshStatus()
        refreshUpdateStatus()
        showBackgroundSetupReminderOnce()
        if (config != null) checkForUpdate()
    }

    override fun onResume() {
        super.onResume()
        if (::permissionStatus.isInitialized) refreshStatus()
        if (::updateStatus.isInitialized) refreshUpdateStatus()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == RECEIVE_SMS_REQUEST || requestCode == OPTIONAL_PHONE_METADATA_REQUEST || requestCode == CAMERA_REQUEST) refreshStatus()
        if (requestCode == NOTIFICATION_REQUEST) refreshStatus()
        if (requestCode == READ_SMS_REQUEST) refreshStatus()
        if (requestCode == CAMERA_REQUEST && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            startQrEnrollment()
        }
    }

    private fun buildContent(): ScrollView {
        val density = resources.displayMetrics.density
        fun dp(value: Int): Int = (value * density).toInt()

        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(24), dp(20), dp(24))
        }
        container.addView(TextView(this).apply {
            text = "Relay短信转发"
            textSize = 24f
        })
        container.addView(TextView(this).apply {
            text = "仅转发本机收到的短信。可使用 HTTP 或 HTTPS，只连接你自己管理的平台。"
            setPadding(0, dp(8), 0, dp(16))
        })

        baseUrlInput = input("平台地址，例如 http://192.168.5.7:8000")
        deviceIdInput = input("设备 ID")
        secretInput = input("HMAC 密钥（只在保存时填写）").apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        container.addView(baseUrlInput)
        container.addView(deviceIdInput)
        container.addView(secretInput)
        container.addView(Button(this).apply {
            text = "扫描后台二维码自动配置"
            setOnClickListener { startQrEnrollment() }
        })

        container.addView(Button(this).apply {
            text = "保存配置"
            setOnClickListener { saveConfig() }
        })
        container.addView(Button(this).apply {
            text = "授予短信权限"
            setOnClickListener {
                requestPermissions(arrayOf(Manifest.permission.RECEIVE_SMS), RECEIVE_SMS_REQUEST)
            }
        })
        container.addView(Button(this).apply {
            text = "Grant optional SIM metadata permissions"
            setOnClickListener {
                requestPermissions(optionalPhoneMetadataPermissions(), OPTIONAL_PHONE_METADATA_REQUEST)
            }
        })
        container.addView(Button(this).apply {
            text = "隐藏应用窗口（从最近任务移除）"
            setOnClickListener { finishAndRemoveTask() }
        })
        container.addView(Button(this).apply {
            text = if (AccessibilityAutoToggle.canWriteSecureSettings(this@MainActivity)) {
                "一键开启无障碍保活"
            } else {
                "设置可选无障碍保活（受限时先允许）"
            }
            setOnClickListener {
                if (AccessibilityAutoToggle.canWriteSecureSettings(this@MainActivity)) {
                    // restart() 内有 1 秒重绑等待，放后台线程避免阻塞 UI
                    Thread {
                        val ok = if (AccessibilityKeepAlive.isEnabled(this@MainActivity)) {
                            AccessibilityAutoToggle.restart(this@MainActivity)
                        } else {
                            AccessibilityAutoToggle.enable(this@MainActivity)
                        }
                        runOnUiThread {
                            if (ok) {
                                getSharedPreferences(KEEP_ALIVE_PREFS, MODE_PRIVATE)
                                    .edit().putBoolean(KEY_A11Y_WANTED, true).apply()
                            }
                            Toast.makeText(
                                this@MainActivity,
                                if (ok) "无障碍保活已开启" else "开启失败，请尝试手动开启",
                                Toast.LENGTH_SHORT,
                            ).show()
                            refreshStatus()
                        }
                    }.start()
                } else {
                    AccessibilityKeepAlive.openSettings(this@MainActivity)
                }
            }
        })
        container.addView(Button(this).apply {
            text = "打开电池优化设置（手动设为不限制）"
            setOnClickListener { BackgroundRuntime.openBatteryOptimizationSettings(this@MainActivity) }
        })
        val relaySwitch = Button(this).apply {
            setOnClickListener { toggleRelaySwitch() }
        }
        relaySwitchButton = relaySwitch
        container.addView(relaySwitch)
        container.addView(Button(this).apply {
            // 原先这里有两个按钮（"后台运行不受限制" / "打开应用详情（自行找到自启动/省电设置）"），
            // 实际在多数 ROM 上落到的是同一个页面（应用详情 / 省电策略），属重复入口，已合并为一个。
            // 行为：省电策略页 → 应用详情页 → 系统设置首页，逐级兜底，全部包异常。
            text = "打开省电/自启动设置（自行设为“不受限制”）"
            setOnClickListener { BackgroundRuntime.openBackgroundRestrictionSettings(this@MainActivity) }
        })
        container.addView(TextView(this).apply {
            text = "远程控制指令授权（默认全部关闭；关闭时设备只回传未授权说明）"
            setPadding(0, dp(16), 0, dp(4))
        })
        val commandSettings = CommandSettingsStore(this)
        listOf(
            CommandSettingsStore.COMMAND_QUERY_BATTERY to "允许查询电量",
            CommandSettingsStore.COMMAND_QUERY_RECENT_SMS to "允许查询设备最近短信（需短信读取权限）",
            CommandSettingsStore.COMMAND_TRIGGER_UPLOAD to "允许触发立即上报",
        ).forEach { (commandType, label) ->
            container.addView(CheckBox(this).apply {
                text = label
                isChecked = commandSettings.isAuthorized(commandType)
                setOnCheckedChangeListener { _, checked ->
                    commandSettings.setAuthorized(commandType, checked)
                }
            })
        }
        container.addView(Button(this).apply {
            text = "授予短信读取权限（查询最近短信用）"
            setOnClickListener {
                requestPermissions(arrayOf(Manifest.permission.READ_SMS), READ_SMS_REQUEST)
            }
        })
        if (BackgroundRuntime.needsNotificationPermission(Build.VERSION.SDK_INT)) {
            container.addView(Button(this).apply {
                text = "授予后台通知权限"
                setOnClickListener {
                    requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), NOTIFICATION_REQUEST)
                }
            })
        }
        container.addView(Button(this).apply {
            text = "检查软件更新"
            setOnClickListener { checkForUpdate() }
        })
        installUpdateButton = Button(this).apply {
            text = "下载并安装更新"
            visibility = View.GONE
            setOnClickListener { downloadOrInstallUpdate() }
        }
        container.addView(installUpdateButton)
        permissionStatus = TextView(this).apply { setPadding(0, dp(12), 0, dp(6)) }
        optionalPermissionStatus = TextView(this).apply { setPadding(0, 0, 0, dp(6)) }
        accessibilityStatus = TextView(this).apply { setPadding(0, 0, 0, dp(6)) }
        uploadStatus = TextView(this)
        updateStatus = TextView(this).apply { setPadding(0, dp(8), 0, 0) }
        container.addView(permissionStatus)
        container.addView(optionalPermissionStatus)
        container.addView(accessibilityStatus)
        container.addView(uploadStatus)
        container.addView(updateStatus)

        return ScrollView(this).apply {
            addView(container, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
    }

    private fun input(hintText: String): EditText = EditText(this).apply {
        hint = hintText
        inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        setSingleLine(true)
    }

    private fun saveConfig() {
        try {
            val existing = SmsRelayRuntime.config(this)
            val secret = secretInput.text.toString().trim().ifBlank { existing?.hmacSecret.orEmpty() }
            val config = SmsRelayConfig(
                baseUrl = SmsRelayConfig.validateBaseUrl(baseUrlInput.text.toString()),
                deviceId = deviceIdInput.text.toString().trim(),
                hmacSecret = secret,
            )
            configStore.save(config)
            SmsRelayRuntime.updateConfig(this, config)
            secretInput.text.clear()
            // 显式保存配置 = 用户要求开始转发，覆盖之前的"暂停"状态。
            RelaySwitch.setEnabled(this, true)
            BackgroundRuntime.start(this)
            KeepAliveWatchdog.enqueuePeriodicWorker(this)
            Thread {
                SmsRelayRuntime.repository(this).resumeAfterCredentialUpdate()
                BackgroundRuntime.requestImmediateUpload(this)
            }.start()
            // 对齐 GKD：若已授予 WRITE_SECURE_SETTINGS，保存配置时自动开启无障碍保活，
            // 无需用户手动进系统设置页——这是"被杀后系统能自动拉回进程"的核心前提。
            if (AccessibilityAutoToggle.canWriteSecureSettings(this) &&
                !AccessibilityKeepAlive.isEnabled(this)
            ) {
                if (AccessibilityAutoToggle.enable(this)) {
                    getSharedPreferences(KEEP_ALIVE_PREFS, MODE_PRIVATE)
                        .edit().putBoolean(KEY_A11Y_WANTED, true).apply()
                }
            }
            Toast.makeText(this, "配置已保存", Toast.LENGTH_SHORT).show()
            refreshStatus()
        } catch (error: IllegalArgumentException) {
            Toast.makeText(this, error.message ?: "配置无效", Toast.LENGTH_LONG).show()
        }
    }

    private fun startQrEnrollment() {
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.CAMERA), CAMERA_REQUEST)
            return
        }
        IntentIntegrator(this)
            .setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
            .setPrompt("扫描后台生成的设备配置二维码")
            .setBeepEnabled(false)
            .setOrientationLocked(false)
            .initiateScan()
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        val result: IntentResult? = IntentIntegrator.parseActivityResult(requestCode, resultCode, data)
        if (result != null) {
            if (!result.contents.isNullOrBlank()) enrollFromQr(result.contents)
            else Toast.makeText(this, "已取消扫码", Toast.LENGTH_SHORT).show()
            return
        }
        super.onActivityResult(requestCode, resultCode, data)
    }

    private fun enrollFromQr(raw: String) {
        uploadStatus.text = "正在验证二维码并获取设备配置…"
        Thread {
            val result = runCatching {
                EnrollmentClient().consume(EnrollmentQrPayload.parse(raw))
            }
            runOnUiThread {
                result.onSuccess { enrollment ->
                    configStore.save(enrollment.config)
                    SmsRelayRuntime.updateConfig(this, enrollment.config)
                    baseUrlInput.setText(enrollment.config.baseUrl)
                    deviceIdInput.setText(enrollment.config.deviceId)
                    secretInput.text.clear()
                    RelaySwitch.setEnabled(this, true)
                    BackgroundRuntime.start(this)
                    Toast.makeText(
                        this,
                        "扫码配置成功${enrollment.deviceName?.let { "：$it" } ?: ""}",
                        Toast.LENGTH_LONG,
                    ).show()
                    refreshStatus()
                }.onFailure { error ->
                    uploadStatus.text = "扫码配置失败：${error.message ?: "二维码无效"}"
                    Toast.makeText(this, error.message ?: "扫码配置失败", Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }

    private fun refreshStatus() {
        permissionStatus.text = if (checkSelfPermission(Manifest.permission.RECEIVE_SMS) == PackageManager.PERMISSION_GRANTED) {
            "短信权限：已授予"
        } else {
            "短信权限：未授予，应用不会收到短信广播"
        }
        val phoneStateGranted = checkSelfPermission(Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED
        val phoneNumberGranted = Build.VERSION.SDK_INT < Build.VERSION_CODES.O ||
            checkSelfPermission(Manifest.permission.READ_PHONE_NUMBERS) == PackageManager.PERMISSION_GRANTED
        optionalPermissionStatus.text = when {
            phoneStateGranted && phoneNumberGranted -> "Optional SIM metadata permissions: granted"
            phoneStateGranted || phoneNumberGranted ->
                "Optional SIM metadata permissions: partially granted; SMS capture is unaffected"
            else -> "Optional SIM metadata permissions: not granted; SMS capture is unaffected"
        }
        accessibilityStatus.text = if (AccessibilityKeepAlive.isEnabled(this)) {
            "无障碍保活：已启用（不读取页面内容）"
        } else {
            "无障碍保活：未启用（如提示受限制，请先在应用信息中允许）"
        }
        if (AccessibilityKeepAlive.isEnabled(this) &&
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.M &&
            !android.provider.Settings.canDrawOverlays(this)
        ) {
            accessibilityStatus.text = "无障碍保活：已启用，但悬浮窗权限缺失（保活效果减弱）"
        }
        val configured = SmsRelayRuntime.config(this) != null
        val relayEnabled = RelaySwitch.isEnabled(this)
        relaySwitchButton?.text = if (relayEnabled) "暂停转发（停止后台保活）" else "恢复转发"
        val lastResult = SmsRelayStatusStore(this).lastResult()
        uploadStatus.text = buildString {
            append(if (configured) "上报配置：已保存" else "上报配置：未完成")
            append("\n后台服务：")
                .append(
                    when {
                        !configured -> "保存配置后启动"
                        !relayEnabled -> "已暂停（点上方“恢复转发”或通知栏磁贴恢复）"
                        else -> "已启动，通知栏常驻"
                    },
                )
            append("\n电池优化：")
                .append(if (BackgroundRuntime.isIgnoringBatteryOptimizations(this@MainActivity)) "已关闭" else "仍受系统优化限制（点下方按钮去设置）")
            append("\n后台限制：")
                .append(if (BackgroundRuntime.isIgnoringBatteryOptimizations(this@MainActivity)) "已不受限" else "建议设为不受限制（点下方按钮）")
            if (BackgroundRuntime.needsNotificationPermission(Build.VERSION.SDK_INT)) {
                append("\n后台通知：")
                    .append(if (BackgroundRuntime.hasNotificationPermission(this@MainActivity)) "已允许" else "未允许")
            }
            if (lastResult != null) append("\n最近上报：").append(lastResult)
        }
    }

    /** 主界面"暂停/恢复转发"：与通知栏磁贴共用同一个持久开关。 */
    private fun toggleRelaySwitch() {
        val enable = !RelaySwitch.isEnabled(this)
        RelaySwitch.setEnabled(this, enable)
        if (enable) {
            BackgroundRuntime.start(this)
            KeepAliveWatchdog.enqueuePeriodicWorker(this)
        } else {
            BackgroundRuntime.stop(this)
            KeepAliveWatchdog.cancel(this)
        }
        refreshStatus()
    }

    private fun optionalPhoneMetadataPermissions(): Array<String> = buildList {
        add(Manifest.permission.READ_PHONE_STATE)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            add(Manifest.permission.READ_PHONE_NUMBERS)
        }
    }.toTypedArray()

    private fun checkForUpdate() {
        val config = SmsRelayRuntime.config(this)
        if (config == null) {
            AppUpdateStore(this).recordStatus("请先保存平台地址，再检查更新")
            refreshUpdateStatus()
            return
        }
        updateStatus.text = "正在检查更新…"
        Thread {
            val result = runCatching {
                val currentVersionCode = currentVersionCode()
                AppUpdateClient().check(config.baseUrl, currentVersionCode)
            }
            runOnUiThread {
                result.onSuccess { check ->
                    val release = check.release
                    val currentVersionCode = currentVersionCode()
                    if (
                        check.updateAvailable &&
                        release != null &&
                        AppUpdatePolicy.canOffer(release, currentVersionCode, Build.VERSION.SDK_INT)
                    ) {
                        AppUpdateStore(this).apply {
                            saveAvailableRelease(release)
                            recordStatus("发现新版本 ${release.versionName}：${release.notes}")
                        }
                    } else {
                        AppUpdateStore(this).apply {
                            clearAvailableRelease()
                            recordStatus("当前已是最新版本")
                        }
                    }
                }.onFailure {
                    AppUpdateStore(this).recordStatus("更新检查失败，请检查平台地址和网络")
                }
                refreshUpdateStatus()
            }
        }.start()
    }

    private fun currentVersionCode(): Long {
        val info = packageManager.getPackageInfo(packageName, 0)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            info.longVersionCode
        } else {
            @Suppress("DEPRECATION")
            info.versionCode.toLong()
        }
    }

    private fun downloadOrInstallUpdate() {
        val config = SmsRelayRuntime.config(this)
        val store = AppUpdateStore(this)
        val release = store.availableRelease()
        if (config == null || release == null) {
            store.recordStatus("没有可安装的更新")
            refreshUpdateStatus()
            return
        }
        val existing = store.downloadedFile()
        if (existing != null) {
            launchVerifiedUpdate(existing, release)
            return
        }
        installUpdateButton.isEnabled = false
        updateStatus.text = "正在下载并校验 ${release.versionName}…"
        Thread {
            val result = runCatching {
                AppUpdateClient().download(config.baseUrl, release, java.io.File(cacheDir, "updates")).also { file ->
                    AppUpdateVerifier(this).verify(file, release)
                    store.saveDownloadedFile(file)
                }
            }
            runOnUiThread {
                installUpdateButton.isEnabled = true
                result.onSuccess { file ->
                    store.recordStatus("更新包校验通过，正在打开系统安装器")
                    launchVerifiedUpdate(file, release)
                }.onFailure { error ->
                    store.clearDownloadedFile()
                    store.recordStatus("更新失败：${safeUpdateError(error)}")
                    refreshUpdateStatus()
                }
            }
        }.start()
    }

    private fun launchVerifiedUpdate(file: java.io.File, release: AppRelease) {
        val store = AppUpdateStore(this)
        runCatching {
            AppUpdateVerifier(this).verify(file, release)
            AppUpdateInstaller.launch(this, file)
        }.onSuccess { result ->
            store.recordStatus(
                when (result) {
                    AppUpdateInstaller.LaunchResult.PermissionRequired ->
                        "请允许 Relay短信转发安装未知应用，返回后再次点击安装"
                    AppUpdateInstaller.LaunchResult.InstallerOpened ->
                        "系统安装器已打开，请确认更新"
                },
            )
        }.onFailure { error ->
            file.delete()
            store.clearDownloadedFile()
            store.recordStatus("更新包已拒绝：${safeUpdateError(error)}")
        }
        refreshUpdateStatus()
    }

    private fun refreshUpdateStatus() {
        val store = AppUpdateStore(this)
        val release = store.availableRelease()
        updateStatus.text = buildString {
            append("软件版本：").append(packageManager.getPackageInfo(packageName, 0).versionName)
            store.status()?.let { append("\n更新状态：").append(it) }
        }
        installUpdateButton.visibility = if (release == null) View.GONE else View.VISIBLE
        if (release != null) {
            installUpdateButton.text = if (store.downloadedFile() == null) {
                "下载并安装 ${release.versionName}"
            } else {
                "继续安装 ${release.versionName}"
            }
        }
    }

    private fun safeUpdateError(error: Throwable): String = when {
        error.message?.contains("signing certificate", ignoreCase = true) == true -> "签名证书不一致"
        error.message?.contains("hash", ignoreCase = true) == true -> "文件校验失败"
        error.message?.contains("version", ignoreCase = true) == true -> "版本信息不一致"
        error.message?.contains("package", ignoreCase = true) == true -> "安装包身份不一致"
        else -> "网络或文件错误"
    }

    private fun showBackgroundSetupReminderOnce() {
        val preferences = getSharedPreferences(REMINDER_PREFERENCES, MODE_PRIVATE)
        if (preferences.getBoolean(KEY_BACKGROUND_REMINDER_SHOWN, false)) return
        preferences.edit().putBoolean(KEY_BACKGROUND_REMINDER_SHOWN, true).apply()
        AlertDialog.Builder(this)
            .setTitle("后台转发设置")
            .setMessage(
                "为保证锁屏和隐藏应用后仍能及时转发，请在系统中确认：\n\n" +
                    "1. 允许通知并保留常驻通知栏\n" +
                    "2. 打开电池优化设置，把本应用设为“不优化/不受限制”\n" +
                    "3. 耗电管理设为“允许后台行为/不受限制”\n" +
                    "4. 允许应用自启动\n" +
                    "5. 开启无障碍保活（推荐）\n" +
                    "6. 下拉通知栏，把“Relay短信转发”磁贴添加到快捷开关\n\n" +
                    "这些厂商设置无法由应用自动开启，需要手动确认一次。应用会保持常驻通知并在收到短信时立即上报。\n\n" +
                    "可选：用 ADB 授予「写入安全设置」权限后，可一键自动开启/重启无障碍保活：\n" +
                    "adb shell pm grant io.smsrelay.collector android.permission.WRITE_SECURE_SETTINGS",
            )
            .setPositiveButton("知道了", null)
            .show()
    }

    companion object {
        const val RECEIVE_SMS_REQUEST = 1001
        const val OPTIONAL_PHONE_METADATA_REQUEST = 1002
        const val NOTIFICATION_REQUEST = 1003
        const val CAMERA_REQUEST = 1004
        const val READ_SMS_REQUEST = 1005
        const val REMINDER_PREFERENCES = "background_setup_reminder"
        const val KEY_BACKGROUND_REMINDER_SHOWN = "shown_v1"
        const val KEEP_ALIVE_PREFS = "keep_alive_prefs"
        const val KEY_A11Y_WANTED = "a11y_wanted"
    }
}
