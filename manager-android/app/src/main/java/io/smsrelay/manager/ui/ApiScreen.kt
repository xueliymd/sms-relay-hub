package io.smsrelay.manager.ui

import android.content.Context
import android.view.View
import android.widget.ScrollView
import io.smsrelay.manager.net.ApiClientInfo
import io.smsrelay.manager.net.BaseUrl

class ApiScreen(private val context: Context) {
    fun render(
        baseUrl: BaseUrl,
        onCopy: (String) -> Unit,
        apiClients: List<ApiClientInfo>,
        onCreateToken: () -> Unit,
        onRefreshTokens: () -> Unit,
    ): View {
        val root = vertical(context, 8)
        root.addView(sectionTitle(context, "平台接口"))
        listOf(
            "健康检查" to baseUrl.http("/api/v1/health"),
            "短信上报（设备）" to baseUrl.http("/api/v1/ingest/messages"),
            "快捷指令" to baseUrl.http("/api/v1/ingest/shortcut"),
            "MQTT 短信上报" to baseUrl.http("/api/v1/ingest/mqtt"),
            "设备指令轮询" to baseUrl.http("/api/v1/devices/{device_id}/commands/pending"),
            "管理员消息" to baseUrl.http("/api/v1/admin/messages"),
            "实时短信 WebSocket" to baseUrl.webSocket("/api/v1/ws/admin/messages"),
            "OTP Claim" to baseUrl.http("/api/v1/otp/claim"),
            "OTP WebSocket" to baseUrl.webSocket("/api/v1/ws/otp"),
        ).forEach { (label, address) ->
            root.addView(bodyText(context, label))
            root.addView(bodyText(context, address))
            root.addMatch(actionButton(context, "复制 " + label) { onCopy(address) })
        }

        root.addView(sectionTitle(context, "脚本 API 令牌"))
        val tokenCard = sectionCard(context, "令牌列表（${apiClients.size}）")
        if (apiClients.isEmpty()) {
            tokenCard.addMatch(bodyText(context, "暂无脚本令牌。点下方按钮创建，创建后请立即复制保存。"))
        } else {
            apiClients.forEach { client ->
                tokenCard.addMatch(
                    bodyText(
                        context,
                        "创建于 " + client.createdAt + (if (client.active) " · 启用" else " · 已停用"),
                    ),
                )
                val token = client.token
                if (token != null) {
                    tokenCard.addMatch(actionButton(context, "复制令牌") { onCopy(token) })
                } else {
                    tokenCard.addMatch(bodyText(context, "令牌明文不可用（旧令牌未加密保存，需重新创建）"))
                }
            }
        }
        tokenCard.addMatch(actionButton(context, "新建脚本令牌") { onCreateToken() })
        tokenCard.addMatch(actionButton(context, "刷新令牌列表") { onRefreshTokens() })
        addCardTo(root, tokenCard)

        root.addView(bodyText(context, "管理员会话和设备密钥不会在此页面显示。"))
        return ScrollView(context).apply { addView(root) }
    }
}
