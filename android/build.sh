#!/bin/sh
# Build the signed release APK with the self-contained toolchain in ~/.local/android-toolchain.
set -e
cd "$(dirname "$0")"
export JAVA_HOME="$HOME/.local/android-toolchain/jdk17"
export ANDROID_HOME="$HOME/.local/android-toolchain/sdk"
"$HOME/.local/android-toolchain/gradle/bin/gradle" -q assembleRelease "$@"
ls -l app/build/outputs/apk/release/app-release.apk
