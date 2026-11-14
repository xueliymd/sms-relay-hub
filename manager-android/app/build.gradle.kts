plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val managerSigningStoreFile = providers.environmentVariable("RELAY_MANAGER_SIGNING_STORE_FILE").orNull
val managerSigningStorePassword = providers.environmentVariable("RELAY_MANAGER_SIGNING_STORE_PASSWORD").orNull
val managerSigningKeyAlias = providers.environmentVariable("RELAY_MANAGER_SIGNING_KEY_ALIAS").orNull
val managerSigningKeyPassword = providers.environmentVariable("RELAY_MANAGER_SIGNING_KEY_PASSWORD").orNull
val managerReleaseSigningConfigured = listOf(
    managerSigningStoreFile,
    managerSigningStorePassword,
    managerSigningKeyAlias,
    managerSigningKeyPassword,
).all { !it.isNullOrBlank() }

android {
    namespace = "io.smsrelay.manager"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.smsrelay.manager"
        minSdk = 26
        targetSdk = 35
        versionCode = 13
        versionName = "1.0.12"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        buildConfig = true
    }

    signingConfigs {
        if (managerReleaseSigningConfigured) {
            create("managerRelease") {
                storeFile = file(requireNotNull(managerSigningStoreFile))
                storePassword = managerSigningStorePassword
                keyAlias = managerSigningKeyAlias
                keyPassword = managerSigningKeyPassword
            }
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            if (managerReleaseSigningConfigured) signingConfig = signingConfigs.getByName("managerRelease")
        }
    }
}

tasks.configureEach {
    if (name in setOf("assembleRelease", "bundleRelease") && !managerReleaseSigningConfigured) {
        doFirst {
            throw GradleException("Release signing is required. Load RELAY_MANAGER_SIGNING_* environment variables.")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.json:json:20240303")

    testImplementation(kotlin("test"))
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:core-ktx:1.6.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
