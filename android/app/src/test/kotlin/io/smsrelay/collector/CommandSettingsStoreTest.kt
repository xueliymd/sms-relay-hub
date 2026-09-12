package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class CommandSettingsStoreTest {
    @Test
    fun supportedCommandsMatchThePlatformV1Whitelist() {
        // 与后端 store.DEVICE_COMMAND_TYPES 保持一致：三端契约不可单边改动
        assertEquals(
            listOf("query_battery", "query_recent_sms", "trigger_upload"),
            CommandSettingsStore.SUPPORTED_COMMANDS,
        )
    }

    @Test
    fun everySupportedCommandHasAPreferenceKeyMapping() {
        // keyFor 对支持类型返回专用 key（不再走 unauthorized_ 兜底），
        // 无法直接调用私有方法，这里校验常量集合与命令行协议一致。
        val constants = setOf(
            CommandSettingsStore.COMMAND_QUERY_BATTERY,
            CommandSettingsStore.COMMAND_QUERY_RECENT_SMS,
            CommandSettingsStore.COMMAND_TRIGGER_UPLOAD,
        )
        assertEquals(constants.size, CommandSettingsStore.SUPPORTED_COMMANDS.size)
        assertTrue(CommandSettingsStore.SUPPORTED_COMMANDS.containsAll(constants))
    }
}
