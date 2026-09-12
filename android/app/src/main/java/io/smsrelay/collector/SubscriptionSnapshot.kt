package io.smsrelay.collector

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.telephony.SubscriptionManager
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat

data class SubscriptionSnapshot(
    val slotIndex: Int? = null,
    val subscriptionId: Int? = null,
    val carrierName: String? = null,
    val phoneNumber: String? = null,
) {
    fun toMetadata(): Map<String, String> = buildMap {
        subscriptionId?.takeIf { it >= 0 }?.let { put("subscription_id", it.toString()) }
        carrierName.cleanValue()?.let { put("carrier_name", it) }
        phoneNumber.cleanValue()?.let { put("phone_number", it) }
    }
}

internal data class SubscriptionRecord(
    val slotIndex: Int?,
    val subscriptionId: Int?,
    val carrierName: String?,
)

internal interface SubscriptionSnapshotSource {
    fun canReadSubscriptionInfo(): Boolean

    fun canReadPhoneNumber(): Boolean

    fun findSubscriptionById(subscriptionId: Int): SubscriptionRecord?

    fun findSubscriptionBySlot(slotIndex: Int): SubscriptionRecord?

    fun findSingleSubscription(): SubscriptionRecord?

    fun line1Number(subscriptionId: Int): String?
}

internal sealed interface SubscriptionLookupResult {
    data class Found(val record: SubscriptionRecord) : SubscriptionLookupResult

    object Missing : SubscriptionLookupResult

    object Failed : SubscriptionLookupResult
}

class SubscriptionSnapshotProvider internal constructor(
    private val source: SubscriptionSnapshotSource,
) {
    constructor(context: Context) : this(AndroidSubscriptionSnapshotSource(context))

    fun capture(
        slotIndexHint: Int?,
        subscriptionIdHint: Int?,
    ): SubscriptionSnapshot {
        var slotIndex = slotIndexHint?.takeIf { it >= 0 }
        var subscriptionId = subscriptionIdHint?.takeIf { it >= 0 }
        var carrierName: String? = null
        val subscriptionInfoAccess = safeCall { source.canReadSubscriptionInfo() }
        var phoneLookupAllowed = subscriptionInfoAccess == false

        if (subscriptionInfoAccess == true) {
            when (val result = resolveSubscription(subscriptionId, slotIndex)) {
                is SubscriptionLookupResult.Found -> {
                    slotIndex = result.record.slotIndex?.takeIf { it >= 0 } ?: slotIndex
                    subscriptionId = result.record.subscriptionId?.takeIf { it >= 0 }
                    carrierName = result.record.carrierName.cleanValue()
                    phoneLookupAllowed = true
                }
                SubscriptionLookupResult.Missing -> {
                    subscriptionId = null
                    carrierName = null
                    phoneLookupAllowed = false
                }
                SubscriptionLookupResult.Failed -> phoneLookupAllowed = false
            }
        }

        val phoneSubscriptionId = subscriptionId
        val phoneNumber = if (
            phoneLookupAllowed &&
            phoneSubscriptionId != null &&
            safeCall { source.canReadPhoneNumber() } == true
        ) {
            safeCall { source.line1Number(phoneSubscriptionId) }.cleanValue()
        } else {
            null
        }

        return SubscriptionSnapshot(
            slotIndex = slotIndex,
            subscriptionId = subscriptionId,
            carrierName = carrierName,
            phoneNumber = phoneNumber,
        )
    }

    private fun resolveSubscription(
        subscriptionIdHint: Int?,
        slotIndexHint: Int?,
    ): SubscriptionLookupResult {
        var subscriptionHintMissing = false
        var subscriptionHintFailed = false
        if (subscriptionIdHint != null) {
            when (val result = lookup { source.findSubscriptionById(subscriptionIdHint) }) {
                is SubscriptionLookupResult.Found -> return result
                SubscriptionLookupResult.Missing -> subscriptionHintMissing = true
                SubscriptionLookupResult.Failed -> subscriptionHintFailed = true
            }
        }

        if (slotIndexHint != null) {
            return when (val result = lookup { source.findSubscriptionBySlot(slotIndexHint) }) {
                is SubscriptionLookupResult.Found -> result
                SubscriptionLookupResult.Missing -> if (subscriptionHintFailed) {
                    SubscriptionLookupResult.Failed
                } else {
                    SubscriptionLookupResult.Missing
                }
                SubscriptionLookupResult.Failed -> SubscriptionLookupResult.Failed
            }
        }

        if (subscriptionIdHint != null) {
            return if (subscriptionHintMissing) {
                SubscriptionLookupResult.Missing
            } else {
                SubscriptionLookupResult.Failed
            }
        }

        return lookup { source.findSingleSubscription() }
    }

    private fun lookup(action: () -> SubscriptionRecord?): SubscriptionLookupResult = try {
        action()?.let(SubscriptionLookupResult::Found) ?: SubscriptionLookupResult.Missing
    } catch (_: SecurityException) {
        SubscriptionLookupResult.Failed
    } catch (_: RuntimeException) {
        SubscriptionLookupResult.Failed
    }

    private fun <T> safeCall(action: () -> T): T? = try {
        action()
    } catch (_: SecurityException) {
        null
    } catch (_: RuntimeException) {
        null
    }
}

private class AndroidSubscriptionSnapshotSource(context: Context) : SubscriptionSnapshotSource {
    private val appContext = context.applicationContext

    override fun canReadSubscriptionInfo(): Boolean =
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP_MR1 &&
            hasPermission(Manifest.permission.READ_PHONE_STATE)

    override fun canReadPhoneNumber(): Boolean =
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            hasPermission(Manifest.permission.READ_PHONE_NUMBERS)

    @SuppressLint("MissingPermission")
    override fun findSubscriptionById(subscriptionId: Int): SubscriptionRecord? {
        if (!hasPermission(Manifest.permission.READ_PHONE_STATE)) return null
        val manager = appContext.getSystemService(SubscriptionManager::class.java) ?: return null
        return manager.getActiveSubscriptionInfo(subscriptionId)?.toRecord()
    }

    @SuppressLint("MissingPermission")
    override fun findSubscriptionBySlot(slotIndex: Int): SubscriptionRecord? {
        if (!hasPermission(Manifest.permission.READ_PHONE_STATE)) return null
        val manager = appContext.getSystemService(SubscriptionManager::class.java) ?: return null
        return manager.activeSubscriptionInfoList
            .orEmpty()
            .firstOrNull { it.simSlotIndex == slotIndex }
            ?.toRecord()
    }

    @SuppressLint("MissingPermission")
    override fun findSingleSubscription(): SubscriptionRecord? {
        if (!hasPermission(Manifest.permission.READ_PHONE_STATE)) return null
        val manager = appContext.getSystemService(SubscriptionManager::class.java) ?: return null
        return manager.activeSubscriptionInfoList.orEmpty().singleOrNull()?.toRecord()
    }

    @SuppressLint("MissingPermission", "HardwareIds")
    @Suppress("DEPRECATION")
    override fun line1Number(subscriptionId: Int): String? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O ||
            !hasPermission(Manifest.permission.READ_PHONE_NUMBERS)
        ) return null
        val manager = appContext.getSystemService(TelephonyManager::class.java) ?: return null
        return manager.createForSubscriptionId(subscriptionId).line1Number
    }

    private fun hasPermission(permission: String): Boolean =
        ContextCompat.checkSelfPermission(appContext, permission) == PackageManager.PERMISSION_GRANTED
}

private fun android.telephony.SubscriptionInfo.toRecord(): SubscriptionRecord = SubscriptionRecord(
    slotIndex = simSlotIndex,
    subscriptionId = subscriptionId,
    carrierName = carrierName?.toString(),
)

private fun String?.cleanValue(): String? = this?.trim()?.takeIf { it.isNotEmpty() }
