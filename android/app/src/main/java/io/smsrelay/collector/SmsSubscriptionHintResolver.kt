package io.smsrelay.collector

data class SmsSubscriptionHints(
    val slotIndex: Int?,
    val subscriptionId: Int?,
)

object SmsSubscriptionHintResolver {
    fun resolve(
        extras: Map<String, Any?>,
        messageSubscriptionIds: List<Int?>,
    ): SmsSubscriptionHints = SmsSubscriptionHints(
        slotIndex = firstInt(extras, SLOT_KEYS),
        subscriptionId = firstInt(extras, SUBSCRIPTION_KEYS)
            ?: messageSubscriptionIds.firstNotNullOfOrNull { it?.takeIf(::isValidId) },
    )

    fun relevantKeys(): Set<String> = (SLOT_KEYS + SUBSCRIPTION_KEYS).toSet()

    private fun firstInt(extras: Map<String, Any?>, keys: List<String>): Int? =
        keys.firstNotNullOfOrNull { key -> parseInt(extras[key]) }

    private fun parseInt(value: Any?): Int? = when (value) {
        is Number -> value.toInt().takeIf(::isValidId)
        is String -> value.trim().toIntOrNull()?.takeIf(::isValidId)
        is IntArray -> value.firstOrNull(::isValidId)
        is LongArray -> value.firstOrNull { it in 0..Int.MAX_VALUE.toLong() }?.toInt()
        is Array<*> -> value.asSequence().mapNotNull(::parseInt).firstOrNull()
        is Iterable<*> -> value.asSequence().mapNotNull(::parseInt).firstOrNull()
        else -> null
    }

    private fun isValidId(value: Int): Boolean = value >= 0

    private val SLOT_KEYS = listOf(
        "android.telephony.extra.SLOT_INDEX",
        "slot",
        "slot_id",
        "slotId",
        "sim_slot",
        "simSlot",
    )

    private val SUBSCRIPTION_KEYS = listOf(
        "android.telephony.extra.SUBSCRIPTION_INDEX",
        "subscription",
        "subscription_id",
        "subscriptionId",
        "sub_id",
        "subId",
    )
}
