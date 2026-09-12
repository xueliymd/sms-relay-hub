package io.smsrelay.collector

sealed interface UploadOutcome {
    data object Uploaded : UploadOutcome
    data object Conflict : UploadOutcome
    data object AuthBlocked : UploadOutcome
    data object Retryable : UploadOutcome
    data object PermanentFailure : UploadOutcome

    companion object {
        fun fromStatus(status: Int): UploadOutcome = when {
            status in 200..299 -> Uploaded
            status == 409 -> Conflict
            status == 401 || status == 403 -> AuthBlocked
            status == 429 || status >= 500 -> Retryable
            else -> PermanentFailure
        }
    }
}
