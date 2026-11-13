package io.smsrelay.manager.ui

import android.content.Context
import android.view.View
import android.widget.LinearLayout
import android.widget.ScrollView
import io.smsrelay.manager.model.AdminDevice
import io.smsrelay.manager.model.DeviceCommand
import io.smsrelay.manager.model.SimProfile
import io.smsrelay.manager.model.commandTypeLabel
import io.smsrelay.manager.time.MessageTime

/**
 * 设备与 SIM 页：设备概览 + 设备写操作（创建/轮换/撤销/恢复/永久删除）+
 * SIM 手机号映射修改/删除 + 远程控制（下发指令 / 查看回传结果）。
 * 写操作全部经 MainActivity 的二次确认后执行。
 */
class DeviceScreen(
    private val context: Context,
    private val onCreateDevice: () -> Unit,
    private val onRotate: (AdminDevice) -> Unit,
    private val onRevoke: (AdminDevice) -> Unit,
    private val onRestore: (AdminDevice) -> Unit,
    private val onPurge: (AdminDevice) -> Unit,
    private val onSimEdit: (AdminDevice, SimProfile) -> Unit,
    private val onSimDelete: (AdminDevice, SimProfile) -> Unit,
    private val commands: Map<String, List<DeviceCommand>> = emptyMap(),
    private val onSendCommand: (AdminDevice, String) -> Unit = { _, _ -> },
    private val onRefreshCommands: (AdminDevice) -> Unit = {},
) {
    fun render(devices: List<AdminDevice>, profiles: Map<String, List<SimProfile>>): View {
        val root = vertical(context, 10)
        root.addView(sectionTitle(context, "设备与 SIM"))

        val summaryCard = sectionCard(context, "设备概览")
        val activeCount = devices.count { it.active }
        val onlineCount = devices.count { it.online }
        val warningCount = devices.count { it.credentialReuseWarning || it.duplicateCollectionWarning }
        summaryCard.addMatch(bodyText(context, "总数 ${devices.size} · 启用 $activeCount · 在线 $onlineCount · 告警 $warningCount"))
        summaryCard.addMatch(actionButton(context, "创建设备") { onCreateDevice() })
        addCardTo(root, summaryCard)

        if (devices.isEmpty()) {
            val emptyCard = sectionCard(context, "暂无设备")
            emptyCard.addMatch(bodyText(context, "平台还没有接入设备。可点上方“创建设备”，或在网页管理端创建后完成扫码/凭据配置。"))
            addCardTo(root, emptyCard)
        }

        devices.forEach { device ->
            val card = sectionCard(
                context,
                device.name + (if (device.active) "" else " · 已撤销") + if (device.online) " · 在线" else " · 离线",
            )
            card.addMatch(bodyText(context, "类型 " + clientTypeLabel(device.clientType) + " · 版本 " + (device.clientVersion ?: "未上报")))
            card.addMatch(bodyText(context, "实例 " + (device.clientInstanceId ?: "未上报") + " · 最近 " + (device.lastSeenAt ?: "无")))
            val warnings = buildList {
                if (device.credentialReuseWarning) add("凭据多实例")
                if (device.duplicateCollectionWarning) add("重复采集")
            }
            if (warnings.isNotEmpty()) {
                card.addMatch(bodyText(context, "告警：" + warnings.joinToString("、")))
            }
            profiles[device.id].orEmpty().forEach { sim ->
                card.addMatch(
                    bodyText(
                        context,
                        "SIM" + (sim.slotIndex + 1) + " · " +
                            (sim.phoneNumberMasked ?: "号码未识别") + " · " +
                            (sim.carrierName ?: "运营商未知") + " · subscriptionId " +
                            (sim.subscriptionId?.toString() ?: "未知") + " · " + simSourceLabel(sim.source),
                    ),
                )
                val simRow = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL }
                simRow.addView(
                    actionButton(context, "修改号码") { onSimEdit(device, sim) },
                    LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
                )
                simRow.addView(
                    actionButton(context, "删除映射") { onSimDelete(device, sim) },
                    LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
                )
                card.addMatch(simRow)
            }

            // 设备写操作（全部带二次确认，由 MainActivity 统一处理）
            if (device.active) {
                card.addMatch(actionButton(context, "轮换凭据（旧凭据立即失效）") { onRotate(device) })
                card.addMatch(actionButton(context, "撤销设备（可恢复）") { onRevoke(device) })
                addRemoteControl(context, card, device)
            } else {
                card.addMatch(actionButton(context, "恢复设备（生成新凭据）") { onRestore(device) })
                card.addMatch(actionButton(context, "永久删除（不可恢复）") { onPurge(device) })
                card.addMatch(bodyText(context, "远程控制：设备已撤销，恢复后方可下发指令。"))
            }
            addCardTo(root, card)
        }

        return ScrollView(context).apply { addView(root) }
    }

    /**
     * 远程控制区块：三条白名单指令 + 最近回传结果。
     * 只在已启用设备上渲染 —— 后端对非 active 设备下发会返回 409 device_not_active。
     */
    private fun addRemoteControl(context: Context, card: LinearLayout, device: AdminDevice) {
        card.addMatch(sectionTitle(context, "远程控制"))
        card.addMatch(
            bodyText(
                context,
                "指令由设备轮询执行（采集端最短 30 秒取一次），且需在采集端应用内开启对应指令授权，下发后通常数十秒出结果。",
            ),
        )
        val commandRow = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL }
        commandRow.addView(
            actionButton(context, "查询电量") { onSendCommand(device, "query_battery") },
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
        )
        commandRow.addView(
            actionButton(context, "查最近短信") { onSendCommand(device, "query_recent_sms") },
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
        )
        commandRow.addView(
            actionButton(context, "触发上报") { onSendCommand(device, "trigger_upload") },
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
        )
        card.addMatch(commandRow)

        when (val history = commands[device.id]) {
            null -> card.addMatch(bodyText(context, "指令记录：加载中…"))
            else -> {
                if (history.isEmpty()) {
                    card.addMatch(bodyText(context, "指令记录：暂无"))
                } else {
                    card.addMatch(bodyText(context, "指令记录（最近 " + history.size + " 条）："))
                    history.take(COMPACT_HISTORY_LIMIT).forEach { command ->
                        card.addMatch(bodyText(context, "· " + commandLine(command)))
                    }
                }
                card.addMatch(actionButton(context, "刷新指令状态") { onRefreshCommands(device) })
            }
        }
    }

    private fun commandLine(command: DeviceCommand): String {
        val time = MessageTime.format(command.completedAt ?: command.sentAt ?: command.createdAt)
        val head = commandTypeLabel(command.commandType) + " · " + commandStatusLabel(command.status) + " · " + time
        val summary = command.resultSummary
        return if (summary.isNullOrBlank()) head else head + " · " + summary
    }

    private fun commandStatusLabel(value: String): String = when (value) {
        "pending" -> "等待设备领取（10 分钟内有效）"
        "sent" -> "设备已领取，等待回传（30 分钟内有效）"
        "completed" -> "已完成"
        "expired" -> "已过期（设备未领取或未回传）"
        else -> value
    }

    private fun clientTypeLabel(value: String): String = when (value) {
        "android_app" -> "Android 应用"
        "root_module" -> "Android Root"
        "ios_shortcut" -> "iPhone 快捷指令"
        "ve_tweak" -> "iOS Ve"
        "air724" -> "合宙 724 取码"
        "air724_mqtt" -> "合宙 724 MQTT 短信上报"
        else -> value
    }

    private fun simSourceLabel(value: String): String = when (value) {
        "auto" -> "自动识别"
        "manual" -> "手动绑定"
        "vendor" -> "厂商通道"
        else -> value
    }

    private companion object {
        /** 设备卡片内最多展开显示的指令条数，避免长历史把页面撑得过长。 */
        const val COMPACT_HISTORY_LIMIT = 5
    }
}
