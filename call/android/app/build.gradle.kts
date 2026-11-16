import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// A release is signed with a key kept outside the repository: ~/.config/henchline/signing.properties
// (storeFile, storePassword, keyAlias, keyPassword). Without it, only debug builds are signed.
val signing = Properties().apply {
    val file = File(System.getProperty("user.home"), ".config/henchline/signing.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}

android {
    namespace = "io.github.ninjatuna007.henchline"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.ninjatuna007.henchline"
        minSdk = 29
        targetSdk = 35
        versionCode = 5
        versionName = "2.2"
    }
    signingConfigs {
        if (signing.getProperty("storeFile") != null) create("release") {
            storeFile = File(signing.getProperty("storeFile"))
            storePassword = signing.getProperty("storePassword")
            keyAlias = signing.getProperty("keyAlias")
            keyPassword = signing.getProperty("keyPassword")
        }
    }
    buildTypes {
        // A release has the default (generated) henchman phrases: your own voice is in src/debug only
        getByName("release") { signingConfig = signingConfigs.findByName("release") }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")  // pull down the chat list to find every agent
    implementation("org.unifiedpush.android:connector:3.0.10")  // rung awake through the ntfy app, no Google
}
