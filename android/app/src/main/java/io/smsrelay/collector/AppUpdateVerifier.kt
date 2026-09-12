package io.smsrelay.collector

import android.content.Context
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import java.io.File
import java.security.MessageDigest

class AppUpdateVerifier(private val context: Context) {
    fun verify(file: File, release: AppRelease) {
        val packageManager = context.packageManager
        val archive = packageManager.getPackageArchiveInfo(file.absolutePath, packageInfoFlags())
            ?: throw IllegalArgumentException("downloaded APK is invalid")
        require(archive.packageName == context.packageName) { "update package name mismatch" }
        require(versionCode(archive) == release.versionCode) { "update version mismatch" }
        require(release.versionCode > currentVersionCode(packageManager)) { "update version is not newer" }

        val archiveSigners = currentSignerDigests(archive)
        val installed = packageManager.getPackageInfo(context.packageName, packageInfoFlags())
        val installedSigners = authorizedSignerDigests(installed)
        require(archiveSigners.isNotEmpty() && archiveSigners.all(installedSigners::contains)) {
            "update signing certificate mismatch"
        }
        require(archiveSigners.singleOrNull() == release.signerSha256) { "release signer metadata mismatch" }
    }

    private fun currentVersionCode(packageManager: PackageManager): Long =
        versionCode(packageManager.getPackageInfo(context.packageName, 0))

    private fun versionCode(info: PackageInfo): Long = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
        info.longVersionCode
    } else {
        @Suppress("DEPRECATION")
        info.versionCode.toLong()
    }

    @Suppress("DEPRECATION")
    private fun packageInfoFlags(): Int = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
        PackageManager.GET_SIGNING_CERTIFICATES or PackageManager.GET_SIGNATURES
    } else {
        PackageManager.GET_SIGNATURES
    }

    private fun currentSignerDigests(info: PackageInfo): Set<String> =
        signerDigests(selectCurrentSigners(info))

    private fun authorizedSignerDigests(info: PackageInfo): Set<String> =
        signerDigests(selectAuthorizedSigners(info))

    @Suppress("DEPRECATION")
    private fun selectCurrentSigners(info: PackageInfo): Set<android.content.pm.Signature> {
        val inputs = signingInputs(info)
        return AppSigningCertificateSelector.current(
            hasMultipleSigners = inputs.hasMultipleSigners,
            apkContentsSigners = inputs.apkContentsSigners,
            signingCertificateHistory = inputs.signingCertificateHistory,
            legacySigners = inputs.legacySigners,
        )
    }

    @Suppress("DEPRECATION")
    private fun selectAuthorizedSigners(info: PackageInfo): Set<android.content.pm.Signature> {
        val inputs = signingInputs(info)
        return AppSigningCertificateSelector.authorized(
            hasMultipleSigners = inputs.hasMultipleSigners,
            apkContentsSigners = inputs.apkContentsSigners,
            signingCertificateHistory = inputs.signingCertificateHistory,
            legacySigners = inputs.legacySigners,
        )
    }

    @Suppress("DEPRECATION")
    private fun signingInputs(info: PackageInfo): SigningInputs {
        val legacySigners = info.signatures.orEmpty().toList()
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) {
            return SigningInputs(false, emptyList(), emptyList(), legacySigners)
        }
        val signingInfo = info.signingInfo
        return SigningInputs(
            hasMultipleSigners = signingInfo?.hasMultipleSigners() == true,
            apkContentsSigners = signingInfo?.apkContentsSigners.orEmpty().toList(),
            signingCertificateHistory = signingInfo?.signingCertificateHistory.orEmpty().toList(),
            legacySigners = legacySigners,
        )
    }

    private fun signerDigests(signatures: Set<android.content.pm.Signature>): Set<String> =
        signatures.mapTo(mutableSetOf()) { signature ->
            MessageDigest.getInstance("SHA-256")
                .digest(signature.toByteArray())
                .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        }

    private data class SigningInputs(
        val hasMultipleSigners: Boolean,
        val apkContentsSigners: List<android.content.pm.Signature>,
        val signingCertificateHistory: List<android.content.pm.Signature>,
        val legacySigners: List<android.content.pm.Signature>,
    )
}

object AppSigningCertificateSelector {
    fun <T> current(
        hasMultipleSigners: Boolean,
        apkContentsSigners: List<T>,
        signingCertificateHistory: List<T>,
        legacySigners: List<T>,
    ): Set<T> = when {
        apkContentsSigners.isNotEmpty() -> apkContentsSigners.toSet()
        !hasMultipleSigners && signingCertificateHistory.isNotEmpty() -> setOf(signingCertificateHistory.last())
        else -> legacySigners.toSet()
    }

    fun <T> authorized(
        hasMultipleSigners: Boolean,
        apkContentsSigners: List<T>,
        signingCertificateHistory: List<T>,
        legacySigners: List<T>,
    ): Set<T> = when {
        hasMultipleSigners && apkContentsSigners.isNotEmpty() -> apkContentsSigners.toSet()
        signingCertificateHistory.isNotEmpty() -> signingCertificateHistory.toSet()
        apkContentsSigners.isNotEmpty() -> apkContentsSigners.toSet()
        else -> legacySigners.toSet()
    }
}
