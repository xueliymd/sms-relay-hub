package io.smsrelay.manager.ui

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.view.View
import android.view.ViewGroup
import android.widget.AbsListView
import android.widget.ArrayAdapter
import android.widget.BaseAdapter
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.Spinner
import android.widget.TextView
import io.smsrelay.manager.model.AdminDevice
import io.smsrelay.manager.model.AdminMessage
import io.smsrelay.manager.time.MessageTime

/**
 * 短信页：实时连接状态 + 筛选 + 消息管理 + 短信记录列表。
 *
 * 排版要点（2026-09-12 优化）：上方三块（连接状态/筛选/消息管理）原先各占一张
 * 大卡片，加上重复的"实时短信"标题，把屏幕吃掉大半，列表只剩约一行高，
 * 单条短信（含正文与多个按钮）根本看不全。现在：
 *   - 删掉与页头重复的"实时短信"大标题；
 *   - 连接状态由卡片降为一行小字；
 *   - 筛选去掉了"设备/SIM"两个标签行，两个下拉并排一行；
 *   - 消息管理的两个按钮并排一行。
 * 省下的高度全部让给列表（weight=1），单条短信可完整展示。
 */
class MessageScreen(
    private val context: Context,
    onDevice: (String?) -> Unit,
    onSim: (Int?) -> Unit,
    onLoadMore: () -> Unit,
    onCopy: (String) -> Unit,
    onToggleRead: (AdminMessage) -> Unit,
    onDelete: (AdminMessage) -> Unit,
    onBulkDelete: () -> Unit,
    onRetention: () -> Unit,
) {
    private val deviceLabels = mutableListOf("全部设备")
    private val deviceAdapter = compactSpinnerAdapter(deviceLabels)
    private val simAdapter = compactSpinnerAdapter(listOf("全部 SIM", "SIM1", "SIM2"))
    private val statusText = TextView(context).apply {
        textSize = 13f
        setTextColor(Color.rgb(96, 108, 132))
        setPadding(0, context.dp(2), 0, context.dp(6))
    }
    private val countText = TextView(context).apply {
        textSize = 16f
        setTypeface(typeface, Typeface.BOLD)
        setTextColor(Color.rgb(25, 39, 67))
        setPadding(0, context.dp(6), 0, context.dp(2))
    }
    private val emptyText = bodyText(context, "暂无短信记录")
    private val loadMoreButton = Button(context)
    private val messageAdapter = MessageAdapter(onCopy, onToggleRead, onDelete)
    private lateinit var deviceSpinner: Spinner
    private lateinit var simSpinner: Spinner
    private var devices: List<AdminDevice> = emptyList()
    private var selectedDeviceId: String? = null
    private var selectedSimSlot: Int? = null
    private var canLoadMore = false
    private var loadingMore = false
    private var suppressSpinnerCallbacks = false

    val view: View = buildView(onDevice, onSim, onLoadMore, onBulkDelete, onRetention)

    fun update(
        devices: List<AdminDevice>,
        selectedDeviceId: String?,
        selectedSimSlot: Int?,
        messages: List<AdminMessage>,
        status: String,
        canLoadMore: Boolean,
        loadingMore: Boolean,
    ) {
        this.devices = devices
        this.selectedDeviceId = selectedDeviceId
        this.selectedSimSlot = selectedSimSlot
        this.canLoadMore = canLoadMore
        this.loadingMore = loadingMore

        statusText.text = status
        countText.text = "短信记录（${messages.size}）"
        messageAdapter.submit(messages)
        emptyText.visibility = if (messages.isEmpty()) View.VISIBLE else View.GONE
        loadMoreButton.visibility = if (canLoadMore) View.VISIBLE else View.GONE
        loadMoreButton.text = if (loadingMore) "正在加载更早短信…" else "加载更早短信"
        loadMoreButton.isEnabled = !loadingMore
        refreshSpinners()
    }

    private fun buildView(
        onDevice: (String?) -> Unit,
        onSim: (Int?) -> Unit,
        onLoadMore: () -> Unit,
        onBulkDelete: () -> Unit,
        onRetention: () -> Unit,
    ): View {
        val root = vertical(context, 10)

        // 一行小字代替原来的"连接状态"卡片（页头已有同一句状态，不重复展示）。
        root.addMatch(statusText)

        // 筛选：两个下拉并排一行，不再各带一行标签。
        val filterRow = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL }
        filterRow.addView(deviceFilter(onDevice), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        filterRow.addView(simFilter(onSim), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        addCardTo(root, compactCard().apply { addMatch(filterRow) })

        // 消息管理：两个按钮并排一行。
        val opsRow = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL }
        opsRow.addView(
            actionButton(context, "批量删除当前列表") { onBulkDelete() },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f),
        )
        opsRow.addView(
            actionButton(context, "数据保留清理") { onRetention() },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f),
        )
        addCardTo(root, compactCard().apply { addMatch(opsRow) })

        root.addMatch(countText)
        emptyText.visibility = View.GONE
        root.addMatch(emptyText)
        loadMoreButton.setOnClickListener {
            if (!loadingMore) onLoadMore()
        }

        val listView = ListView(context).apply {
            divider = null
            dividerHeight = 0
            addFooterView(loadMoreButton, null, false)
            adapter = messageAdapter
            setOnScrollListener(object : AbsListView.OnScrollListener {
                override fun onScrollStateChanged(view: AbsListView?, scrollState: Int) = Unit
                override fun onScroll(
                    view: AbsListView?,
                    firstVisibleItem: Int,
                    visibleItemCount: Int,
                    totalItemCount: Int,
                ) {
                    if (canLoadMore && !loadingMore && totalItemCount > 0 &&
                        firstVisibleItem + visibleItemCount >= totalItemCount - 2
                    ) {
                        onLoadMore()
                    }
                }
            })
        }
        root.addView(
            listView,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f),
        )
        return root
    }

    /** 紧凑卡片：去掉标题、收窄内边距，仅保留圆角描边。 */
    private fun compactCard(): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(context.dp(8), context.dp(4), context.dp(8), context.dp(4))
        background = android.graphics.drawable.GradientDrawable().apply {
            setColor(Color.WHITE)
            cornerRadius = context.dp(10).toFloat()
            setStroke(context.dp(1), Color.rgb(213, 222, 237))
        }
    }

    /** 下拉用紧凑布局：15sp 文本 + 小内边距，替代系统默认的偏大行高。 */
    private fun compactSpinnerAdapter(labels: List<String>): ArrayAdapter<String> =
        object : ArrayAdapter<String>(context, android.R.layout.simple_spinner_item, labels) {
            override fun getView(position: Int, convertView: View?, parent: ViewGroup): View =
                (super.getView(position, convertView, parent) as TextView).apply {
                    textSize = 15f
                    setTextColor(Color.rgb(25, 39, 67))
                    setPadding(context.dp(4), context.dp(2), context.dp(4), context.dp(2))
                    minHeight = 0
                }
        }

    private fun deviceFilter(onDevice: (String?) -> Unit): View =
        Spinner(context).apply {
            adapter = deviceAdapter
            onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                    if (suppressSpinnerCallbacks) return
                    if (position == 0) {
                        if (selectedDeviceId != null) onDevice(null)
                    } else {
                        val activeDevices = devices.filter { it.active }
                        activeDevices.getOrNull(position - 1)?.id?.takeIf { it != selectedDeviceId }?.let(onDevice)
                    }
                }
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) = Unit
            }
        }.also { deviceSpinner = it }

    private fun simFilter(onSim: (Int?) -> Unit): View =
        Spinner(context).apply {
            adapter = simAdapter
            onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                    if (suppressSpinnerCallbacks) return
                    val value = listOf<Int?>(null, 0, 1).getOrNull(position)
                    if (value != selectedSimSlot) onSim(value)
                }
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) = Unit
            }
        }.also { simSpinner = it }

    private fun refreshSpinners() {
        suppressSpinnerCallbacks = true
        val activeDevices = devices.filter { it.active }
        deviceLabels.clear()
        deviceLabels.add("全部设备")
        activeDevices.forEach { device -> deviceLabels.add(device.name) }
        deviceAdapter.notifyDataSetChanged()
        deviceSpinner.setSelection(
            selectedDeviceId?.let { id -> activeDevices.indexOfFirst { it.id == id }.takeIf { it >= 0 }?.plus(1) } ?: 0,
            false,
        )
        simSpinner.setSelection(listOf<Int?>(null, 0, 1).indexOf(selectedSimSlot).coerceAtLeast(0), false)
        suppressSpinnerCallbacks = false
    }

    private inner class MessageAdapter(
        private val onCopy: (String) -> Unit,
        private val onToggleRead: (AdminMessage) -> Unit,
        private val onDelete: (AdminMessage) -> Unit,
    ) : BaseAdapter() {
        private var messages: List<AdminMessage> = emptyList()

        fun submit(messages: List<AdminMessage>) {
            this.messages = messages
            notifyDataSetChanged()
        }

        override fun getCount(): Int = messages.size
        override fun getItem(position: Int): AdminMessage = messages[position]
        override fun getItemId(position: Int): Long = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
            val message = getItem(position)
            val deviceName = devices.firstOrNull { it.id == message.deviceId }?.name ?: message.deviceId
            return messageItem(message, deviceName)
        }

        private fun messageItem(message: AdminMessage, deviceName: String): View =
            LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(0, context.dp(8), 0, context.dp(8))
                val readLabel = if (message.readAt != null) "已读" else "未读"
                addMatch(bodyText(context, "$deviceName · ${message.sender} · ${message.simSlot?.let { "SIM" + (it + 1) } ?: "未知 SIM"} · $readLabel"))
                // 短信时间以设备时间优先（快捷指令 received_at / 消息 Date 字段），
                // 只展示一个时间；平台入库时间不再并排显示，避免同一条短信出现两个时间。
                val messageTime = MessageTime.format(message.messageDate ?: message.receivedAt)
                addMatch(
                    bodyText(
                        context,
                        "短信时间：$messageTime · " +
                            (message.receivingPhoneMasked ?: "未识别号码"),
                    ),
                )
                addMatch(bodyText(context, message.body ?: "短信正文已清理"))
                // 验证码单独一行（含号码，长度不定），其余操作合并成一行，
                // 把纵向空间留给短信正文本身，单条短信才能一屏看全。
                val otpCode = message.otpCode
                if (!otpCode.isNullOrBlank()) {
                    addMatch(actionButton(context, "验证码 " + otpCode) { onCopy(otpCode) })
                }
                val actionRow = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL }
                val body = message.body
                if (!body.isNullOrBlank()) {
                    actionRow.addView(
                        compactButton(context, "复制短信") { onCopy(body) },
                        LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
                    )
                }
                actionRow.addView(
                    compactButton(context, if (message.readAt != null) "标为未读" else "标为已读") { onToggleRead(message) },
                    LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
                )
                actionRow.addView(
                    compactButton(context, "删除") { onDelete(message) },
                    LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
                )
                addMatch(actionRow)
            }
    }
}
