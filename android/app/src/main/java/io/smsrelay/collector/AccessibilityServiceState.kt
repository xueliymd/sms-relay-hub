package io.smsrelay.collector

object AccessibilityServiceState {
    fun containsService(
        enabledServices: String?,
        packageName: String,
        serviceClassName: String,
    ): Boolean {
        val fullComponent = "$packageName/$serviceClassName"
        val shortComponent = "$packageName/.RelayKeepAliveAccessibilityService"
        return enabledServices
            ?.split(':')
            ?.asSequence()
            ?.map(String::trim)
            ?.any { it.equals(fullComponent, ignoreCase = true) || it.equals(shortComponent, ignoreCase = true) }
            ?: false
    }
}
