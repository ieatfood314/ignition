#!/bin/sh
# Builds and publishes a release for every commit on GitHub's main that changes the app (android/ or web/,
# which the app bundles), so Obtainium can install it on phones.
#
#   android/autobuild.sh            check once, building if needed (what the timer runs)
#   android/autobuild.sh install    run it every minute with a systemd user timer
#
# For the build machine only: needs the toolchain android/build.sh uses, the signing key, and gh logged in.
set -eu

repo=ieatfood314/ignition
work="${XDG_DATA_HOME:-$HOME/.local/share}/ignition/autobuild"
state="${XDG_STATE_HOME:-$HOME/.local/state}/ignition/autobuild-commit"

install_timer() {
    units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$units"
    cat >"$units/ignition-autobuild.service" <<UNIT
[Unit]
Description=Build and release ignition's Android app for new commits

[Service]
Type=oneshot
ExecStart=/bin/sh $work/android/autobuild.sh
UNIT
    cat >"$units/ignition-autobuild.timer" <<UNIT
[Unit]
Description=Check for new ignition commits to build every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
UNIT
    [ -d "$work/.git" ] || git clone -q "https://github.com/$repo.git" "$work"
    systemctl --user daemon-reload
    systemctl --user enable --now ignition-autobuild.timer
    echo "autobuild: checking every minute (journalctl --user -u ignition-autobuild)"
}

# Everything runs from inside main, which sh has read in full before it starts, because the checkout below
# replaces this very file (the timer runs the copy in $work).
main() {
    [ -d "$work/.git" ] || git clone -q "https://github.com/$repo.git" "$work"
    git -C "$work" fetch -q --tags origin main
    new=$(git -C "$work" rev-parse FETCH_HEAD)
    last=$(cat "$state" 2>/dev/null || true)
    [ "$new" != "$last" ] || return 0
    mkdir -p "$(dirname "$state")"
    # Recorded up front: a commit that fails to build isn't retried every minute; the next one is built.
    echo "$new" >"$state"
    if [ -n "$last" ] && git -C "$work" cat-file -e "$last" 2>/dev/null &&
        git -C "$work" diff --quiet "$last" "$new" -- android web; then
        return 0
    fi

    git -C "$work" checkout -q --force --detach "$new"
    count=$(git -C "$work" rev-list --count HEAD)
    tag="v$(sed -n 's/^ignitionVersion=//p' "$work/android/gradle.properties").$count"
    if gh release view "$tag" -R "$repo" >/dev/null 2>&1; then return 0; fi

    echo "building $tag ($(git -C "$work" log -1 --format='%h %s'))"
    sh "$work/android/build.sh" --no-daemon >/dev/null
    out=$(mktemp -d)
    cp "$work/android/app/build/outputs/apk/release/app-release.apk" "$out/ignition-$tag.apk"
    prev=$(git -C "$work" describe --tags --abbrev=0 --match 'v*' "$new^" 2>/dev/null || true)
    {
        echo "**Install:** download \`ignition-$tag.apk\` below, or get every build automatically through [Obtainium](https://obtainium.imranr.dev/)."
        echo
        echo "### Changes"
        git -C "$work" log --no-merges --format='- %s' "${prev:+$prev..}$new"
    } >"$out/notes.md"
    gh release create "$tag" -R "$repo" --target "$new" --title "Ignition $tag" --notes-file "$out/notes.md" \
        "$out/ignition-$tag.apk"
    rm -rf "$out"
    echo "released $tag"
}

case ${1:-} in
    install) install_timer; exit ;;
    "") main; exit ;;
    *) echo "usage: $0 [install]" >&2; exit 1 ;;
esac
