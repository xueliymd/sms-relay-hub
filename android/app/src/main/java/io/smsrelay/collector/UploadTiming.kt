package io.smsrelay.collector

object UploadTiming {
    fun elapsedMillis(startNanos: Long, endNanos: Long): Long =
        ((endNanos - startNanos) / 1_000_000L).coerceAtLeast(0L)
}
