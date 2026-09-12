package io.smsrelay.manager.ui

import android.content.Context
import android.graphics.Color
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

class DashboardScreen(
    context: Context,
    onPlatform: () -> Unit,
    onMessages: () -> Unit,
    onDevices: () -> Unit,
    onApi: () -> Unit,
    onLogout: () -> Unit,
) {
    val warningText = TextView(context)
    val connectionText = TextView(context)
    val content = FrameLayout(context)
    val view: View

    init {
        val root = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(context.dp(12), context.dp(12), context.dp(12), context.dp(12))
        }
        val titleRow = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL }
        titleRow.addView(sectionTitle(context, "Relay Hub 管理端"), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        titleRow.addView(actionButton(context, "退出登录", onLogout))
        root.addMatch(titleRow)
        warningText.setTextColor(Color.rgb(153, 76, 0))
        warningText.setPadding(0, context.dp(4), 0, context.dp(4))
        root.addMatch(warningText)
        connectionText.setTextColor(Color.rgb(30, 95, 68))
        root.addMatch(connectionText)
        root.addView(content, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        val navigation = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL }
        listOf(
            "短信" to onMessages,
            "平台" to onPlatform,
            "设备/SIM" to onDevices,
            "接口" to onApi,
        ).forEach { (label, action) ->
            navigation.addView(Button(context).apply { text = label; setOnClickListener { action() } }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        }
        root.addMatch(navigation)
        view = root
    }

    fun show(view: View) {
        content.removeAllViews()
        content.addView(view, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
    }
}
