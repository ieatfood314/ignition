#!/bin/sh
# Install the release APK on the phone: over adb if wireless debugging is connected, else via Termux + the installer prompt.
set -e
cd "$(dirname "$0")"
APK=app/build/outputs/apk/release/app-release.apk
if command -v hadb >/dev/null && hadb get-state >/dev/null 2>&1; then
  hadb install -r "$APK"
else
  scp -q "$APK" halley:storage/downloads/ignition.apk
  ssh halley 'termux-open storage/downloads/ignition.apk'
  echo "Installer prompt opened on the phone; tap Install."
fi
