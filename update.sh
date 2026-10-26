#!/bin/sh
# Updates whatever ignition has on this computer to the latest main on GitHub: the app (restarted only when
# its code changed; open pages reload by themselves) and the blocker (reinstalled, settings kept). Made to
# run from topgrade, in ~/.config/topgrade.toml:
#
#   [commands]
#   "ignition" = "curl -fsSL https://raw.githubusercontent.com/ieatfood314/ignition/main/update.sh | sh"
set -eu

repo=https://github.com/ieatfood314/ignition
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ignition.service"
state="${XDG_STATE_HOME:-$HOME/.local/state}/ignition/app-commit"

say() { printf 'ignition: %s\n' "$*"; }
die() { say "$*" >&2; exit 1; }

# Brings a checkout up to GitHub's main, if it's on main and hasn't gone its own way.
pull() {
    [ -d "$1/.git" ] || die "$1 isn't a git checkout, so it can't be updated. Install git and run the install command again."
    git -C "$1" fetch -q origin main
    new=$(git -C "$1" rev-parse FETCH_HEAD)
    if [ "$(git -C "$1" symbolic-ref -q --short HEAD)" = main ] && git -C "$1" merge-base --is-ancestor HEAD "$new"; then
        git -C "$1" merge -q --ff-only "$new"
    fi
}

# Everything runs from inside main, which sh has read in full before it starts, because pulling can replace
# this very file.
main() {
    code=
    if [ -f "$unit" ]; then
        # The app runs from the checkout named in its service.
        code=$(sed -n 's|^ExecStart=.* \(/.*\)/server/ignition\.py.*|\1|p' "$unit")
        pull "$code"
        head=$(git -C "$code" rev-parse HEAD)
        last=$(cat "$state" 2>/dev/null || true)
        if [ "$head" != "$last" ]; then
            if [ -z "$last" ] || ! git -C "$code" diff --quiet "$last" "$head" -- server; then
                systemctl --user restart ignition.service
                say "app restarted on $(git -C "$code" log -1 --format='%h %s')"
            fi
            mkdir -p "$(dirname "$state")"
            echo "$head" >"$state"
        fi
    fi

    if [ -f /etc/systemd/system/ignition-enforcer.service ]; then
        if [ -z "$code" ]; then
            code="${XDG_DATA_HOME:-$HOME/.local/share}/ignition/app"
            if [ -e "$code" ]; then pull "$code"; else git clone -q --depth 1 "$repo.git" "$code"; fi
        fi
        head=$(git -C "$code" rev-parse HEAD)
        if [ "$head" != "$(cat /etc/ignition/commit 2>/dev/null || true)" ]; then
            sudo sh "$code/enforcer/install.sh" >/dev/null
            echo "$head" | sudo tee /etc/ignition/commit >/dev/null
            say "blocker updated to $(git -C "$code" log -1 --format='%h %s')"
        fi
    fi

    # The KDE Plasma widgets go in on the first update that finds Plasma, then stay current unless removed.
    widgets="${XDG_DATA_HOME:-$HOME/.local/share}/plasma/plasmoids/ca.heeney.ignition"
    mark="$(dirname "$state")/widgets-commit"
    if [ -n "$code" ] && [ -d "$code/plasma" ] && command -v kpackagetool6 >/dev/null; then
        head=$(git -C "$code" rev-parse HEAD)
        last=$(cat "$mark" 2>/dev/null || true)
        if [ -z "$last" ] || { [ -d "$widgets" ] && ! git -C "$code" diff --quiet "$last" "$head" -- plasma; }; then
            sh "$code/plasma/install.sh" >/dev/null
            if [ -z "$last" ]; then say "added the KDE Plasma widgets (Add Widgets › search for ignition)"
            else say "Plasma widgets updated; they load when Plasma restarts: systemctl --user restart plasma-plasmashell"; fi
        fi
        mkdir -p "$(dirname "$mark")"
        echo "$head" >"$mark"
    fi

    [ -n "$code" ] || say "not installed on this computer"
}
main "$@"; exit
