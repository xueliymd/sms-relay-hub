package io.smsrelay.manager.ui

import android.content.Context
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.graphics.Typeface
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

internal fun Context.dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

internal fun vertical(context: Context, padding: Int = 16): LinearLayout = LinearLayout(context).apply {
    orientation = LinearLayout.VERTICAL
    setPadding(context.dp(padding), context.dp(padding), context.dp(padding), context.dp(padding))
    layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
}

internal fun sectionTitle(context: Context, textValue: String): TextView = TextView(context).apply {
    text = textValue
    textSize = 20f
    setTypeface(typeface, Typeface.BOLD)
    setTextColor(Color.rgb(25, 39, 67))
    setPadding(0, context.dp(12), 0, context.dp(8))
}

internal fun bodyText(context: Context, textValue: String): TextView = TextView(context).apply {
    text = textValue
    textSize = 14f
    setTextColor(Color.rgb(55, 68, 91))
    setPadding(0, context.dp(4), 0, context.dp(4))
    setTextIsSelectable(true)
}

internal fun sectionCard(context: Context, titleText: String): LinearLayout = LinearLayout(context).apply {
    orientation = LinearLayout.VERTICAL
    setPadding(context.dp(14), context.dp(14), context.dp(14), context.dp(14))
    background = GradientDrawable().apply {
        setColor(Color.rgb(255, 255, 255))
        cornerRadius = context.dp(12).toFloat()
        setStroke(context.dp(1), Color.rgb(213, 222, 237))
    }
    addMatch(TextView(context).apply {
        text = titleText
        textSize = 16f
        setTypeface(typeface, Typeface.BOLD)
        setTextColor(Color.rgb(25, 39, 67))
        setPadding(0, 0, 0, context.dp(8))
    })
}

internal fun addCardTo(parent: LinearLayout, card: LinearLayout) {
    parent.addView(
        card,
        LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            bottomMargin = parent.context.dp(10)
        },
    )
}

internal fun actionButton(context: Context, textValue: String, action: () -> Unit): Button = Button(context).apply {
    text = textValue
    setOnClickListener { action() }
}

/**
 * 紧凑按钮：13sp 文本 + 收窄内边距，用于"一屏要塞多个操作"的行内场景
 * （如短信条目里并排的 复制/已读/删除）。保留约 40dp 的可点击高度。
 */
internal fun compactButton(context: Context, textValue: String, action: () -> Unit): Button = Button(context).apply {
    text = textValue
    textSize = 13f
    minHeight = 0
    minimumHeight = 0
    minWidth = 0
    minimumWidth = 0
    setPadding(context.dp(4), context.dp(6), context.dp(4), context.dp(6))
    setOnClickListener { action() }
}

internal fun LinearLayout.addMatch(view: View) {
    addView(view, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
}
