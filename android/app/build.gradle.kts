import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing: ~/.config/ignition/keystore.properties (storeFile/storePassword/keyAlias/keyPassword).
val keystoreProps = Properties().apply {
    val file = file(System.getProperty("user.home") + "/.config/ignition/keystore.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}

android {
    namespace = "ca.heeney.ignition"
    compileSdk = 35

    defaultConfig {
        applicationId = "ca.heeney.ignition"
        minSdk = 29
        targetSdk = 35
        versionCode = (project.property("ignitionVersionCode") as String).toInt()
        versionName = project.property("ignitionVersionName") as String
    }

    signingConfigs {
        create("release") {
            if (keystoreProps.isNotEmpty()) {
                storeFile = file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    lint {
        checkReleaseBuilds = false
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
    sourceSets["main"].kotlin.srcDir("src/main/kotlin")
    // The app ships the same web UI as the server (../web), talking to a local backend through a JS bridge.
    sourceSets["main"].assets.srcDir(rootProject.file("../web"))
}

dependencies {
    // Real org.json for JVM unit tests (android.jar only has stubs).
    testImplementation("org.json:json:20240303")
    testImplementation("junit:junit:4.13.2")
}
