package io.smsrelay.manager.ui

import android.content.Context
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import io.smsrelay.manager.model.PlatformProfile

class PlatformScreen(
    private val context: Context,
    private val onProfileSelected: (PlatformProfile) -> Unit,
    onSave: () -> Unit,
    onDelete: () -> Unit,
    onTest: () -> Unit,
    onLogin: () -> Unit,
) {
    val profileSpinner = Spinner(context)
    val labelInput = EditText(context)
    val addressInput = EditText(context)
    val usernameInput = EditText(context)
    val passwordInput = EditText(context)
    val rememberPassword = CheckBox(context)
    val statusText = TextView(context)
    val view: View
    private var profiles: List<PlatformProfile> = emptyList()
    private var suppressSelection = false

    init {
        val content = vertical(context)
        content.addView(sectionTitle(context, "Relay Hub 管理端"))
        content.addView(bodyText(context, "只读查看平台状态、设备、SIM、完整短信、验证码和接口地址"))
        content.addView(label("已保存平台"))
        content.addMatch(profileSpinner)
        content.addView(label("平台名称"))
        labelInput.hint = "例如：主服务器"
        content.addMatch(labelInput)
        content.addView(label("平台地址"))
        addressInput.hint = "https://example.com 或 http://IP:端口"
        addressInput.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        content.addMatch(addressInput)
        content.addView(
            bodyText(
                context,
                "模拟器注意：不要填 127.0.0.1 或 10.0.2.2；请填写电脑局域网 IP，例如 http://电脑IP:39400",
            ),
        )
        content.addView(label("管理员用户名"))
        usernameInput.inputType = InputType.TYPE_CLASS_TEXT
        content.addMatch(usernameInput)
        content.addView(label("管理员密码"))
        passwordInput.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        content.addMatch(passwordInput)
        rememberPassword.text = "记住密码"
        content.addMatch(rememberPassword)
        content.addMatch(actionRow("保存平台", onSave, "删除平台", onDelete))
        content.addMatch(actionRow("测试连接", onTest, "登录平台", onLogin))
        statusText.text = "请输入平台地址和管理员账号"
        statusText.setPadding(0, context.dp(12), 0, context.dp(12))
        content.addMatch(statusText)
        view = ScrollView(context).apply { addView(content) }

        profileSpinner.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (!suppressSelection) profiles.getOrNull(position)?.let(onProfileSelected)
            }
            override fun onNothingSelected(parent: android.widget.AdapterView<*>?) = Unit
        }
    }

    fun setProfiles(next: List<PlatformProfile>, selectedId: String?) {
        profiles = next
        suppressSelection = true
        profileSpinner.adapter = ArrayAdapter(
            context,
            android.R.layout.simple_spinner_dropdown_item,
            next.map { it.label + " · " + it.baseUrl },
        )
        val selectedIndex = next.indexOfFirst { it.id == selectedId }.takeIf { it >= 0 } ?: 0
        if (next.isNotEmpty()) profileSpinner.setSelection(selectedIndex, false)
        suppressSelection = false
    }

    fun showProfile(profile: PlatformProfile, password: String?) {
        labelInput.setText(profile.label)
        addressInput.setText(profile.baseUrl)
        usernameInput.setText(profile.username)
        rememberPassword.isChecked = profile.rememberPassword
        passwordInput.setText(password.orEmpty())
    }

    private fun label(textValue: String): TextView = bodyText(context, textValue)

    private fun actionRow(firstText: String, first: () -> Unit, secondText: String, second: () -> Unit): LinearLayout =
        LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(actionButton(context, firstText, first), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            addView(actionButton(context, secondText, second), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        }
}
