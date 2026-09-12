plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.kapt")
}

val relaySigningStoreFile = providers.environmentVariable("RELAY_SIGNING_STORE_FILE").orNull
val relaySigningStorePassword = providers.environmentVariable("RELAY_SIGNING_STORE_PASSWORD").orNull
val relaySigningKeyAlias = providers.environmentVariable("RELAY_SIGNING_KEY_ALIAS").orNull
val relaySigningKeyPassword = providers.environmentVariable("RELAY_SIGNING_KEY_PASSWORD").orNull
val relayReleaseSigningConfigured = listOf(
    relaySigningStoreFile,
    relaySigningStorePassword,
    relaySigningKeyAlias,
    relaySigningKeyPassword,
).all { !it.isNullOrBlank() }

android {
    namespace = "io.smsrelay.collector"
    compileSdk = 35

    buildFeatures {
        buildConfig = true
    }

    defaultConfig {
        applicationId = "io.smsrelay.collector"
        minSdk = 26
        targetSdk = 35
        versionCode = 23
        versionName = "1.0.22"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    signingConfigs {
        if (relayReleaseSigningConfigured) {
            create("relayRelease") {
                storeFile = file(requireNotNull(relaySigningStoreFile))
                storePassword = relaySigningStorePassword
                keyAlias = relaySigningKeyAlias
                keyPassword = relaySigningKeyPassword
            }
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            if (relayReleaseSigningConfigured) {
                signingConfig = signingConfigs.getByName("relayRelease")
            }
        }
    }
}

tasks.configureEach {
    if (name in setOf("assembleRelease", "bundleRelease") && !relayReleaseSigningConfigured) {
        doFirst {
            throw GradleException("Release signing is required. Load RELAY_SIGNING_* environment variables.")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.room:room-runtime:2.7.0")
    implementation("androidx.room:room-ktx:2.7.0")
    implementation("androidx.work:work-runtime-ktx:2.10.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    implementation("com.google.zxing:core:3.5.3")
    kapt("androidx.room:room-compiler:2.7.0")
    testImplementation(kotlin("test"))
    testImplementation("org.json:json:20240303")
}
