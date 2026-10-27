# ignition: the technical details

Everything the [README](../README.md) leaves out.

## Pieces

There are two independent halves that share a sync protocol.

1. **Server + website.** Python standard library, no build step. Runs on one computer, reachable from your
   other devices over a tailnet. A root **enforcer** (the "blocker") on each computer applies the blocks.
2. **Android app** (`android/`). Works entirely on its own: no account, no server, no Tailscale. Point it at
   a server in Settings and everything syncs both ways.

```
server/ignition.py              web app + JSON API + SQLite + launch scheduler (user service)
web/                            the frontend (also bundled into the Android app)
enforcer/ignition-enforcer.py   root daemon on each computer that applies the blocks
enforcer/openrgb-ram            optional burn/idle RGB hook
enforcer/install.sh             installs the enforcer as a system service
plasma/                         KDE Plasma 6 widgets (QML), talking to the server's API
install.sh                      installs the server as a user service (+ the enforcer, + Tailscale Serve)
uninstall.sh                    removes both, and the browser policies the enforcer set
android/                        the Android app (Kotlin, no dependencies)
```

## Installing on computers

`install.sh` runs as your user and does three things:

1. Installs the server as a `systemd --user` service (`ignition.service`) on `127.0.0.1:4580`. Its data
   lives in `~/.local/share/ignition/ignition.db`.
2. Runs `sudo enforcer/install.sh`, pointing the enforcer at that server. Skip this with `--no-blocker`.
3. If Tailscale is installed, shares the server on the tailnet at `http://<machine>:8445` with Tailscale
   Serve. This is plain HTTP, tailnet only. If Tailscale refuses, run
   `sudo tailscale set --operator=$USER` once and re-run the installer.

`--join URL` does only step 2, pointing the enforcer at a server on another computer. Ports can be changed
with `IGNITION_PORT` and `IGNITION_TAILNET_PORT`.

Piped from `curl`, the installer first clones the repo into `~/.local/share/ignition/app` (or downloads a
tarball if git isn't installed) and runs from there. Running it again pulls the latest version. A burn
started on the server keeps going through updates.

### Updating

`update.sh` updates whatever ignition has on a computer to the latest `main`: it fast-forwards the app's
checkout and restarts the app if `server/` changed (open pages reload themselves when `web/` changes), then
reinstalls the blocker, keeping its settings, if there's a new commit. It's built to run from topgrade:

```toml
[commands]
"ignition" = "curl -fsSL https://raw.githubusercontent.com/ieatfood314/ignition/main/update.sh | sh"
```

On phones, [Obtainium](https://obtainium.imranr.dev/) pointed at this repo installs each new release.

Not on Linux? The server still runs anywhere Python 3 does: `python3 server/ignition.py`, then open
http://127.0.0.1:4580. You just don't get the blocker or the background service.

### Enforcer options

Most people never need these. Run `sudo enforcer/install.sh --help` for the full list. Re-running keeps
options you don't pass. Config lives in `/etc/ignition/enforcer.json`. Edit it and run
`systemctl restart ignition-enforcer` to change it by hand.

| Option | What it does |
| --- | --- |
| `--server-url URL` | Where this computer reaches the server. Defaults to the one on this computer. |
| `--user USER` | Your login, for desktop notifications. Defaults to whoever ran `sudo`. |
| `--host NAME` | Name shown on the Ignite page. Defaults to the hostname. |
| `--no-notify` | No desktop notification when a blocked program is closed. |
| `--block LIST` | Extra programs to kill on this computer only, comma-separated. The shared list is in the app. |
| `--block-ports LIST` | Local TCP ports to close to everyone during a burn, such as a self-hosted video server. |
| `--t3-ports LIST` | [T3 Code](https://github.com/pingdotgg/t3code) ports to cut off during a burn, e.g. `3773`. |
| `--hook-burn CMD` | Shell command to run when a burn starts. `$IGNITION_COLOR` is the accent, e.g. `e23a29`. |
| `--hook-idle CMD` | Shell command to run when it ends. |

`--block-ports` and `--t3-ports` need nftables (`apt/dnf/pacman install nftables`).

For example, a home server that hosts ignition and a media server, and a desktop that follows it:

```sh
sudo enforcer/install.sh --t3-ports 3773,13773 --block-ports 8000
sudo enforcer/install.sh --server-url http://nas:8445 --block topgrade,lutris --t3-ports 3773 \
  --hook-burn '/usr/local/lib/ignition/openrgb-ram burn' \
  --hook-idle '/usr/local/lib/ignition/openrgb-ram idle'
```

The `openrgb-ram` hook recolours RGB RAM to ignition's accent during a burn and puts it back afterwards,
through OpenRGB. It runs OpenRGB with its own config (`/etc/ignition/openrgb-ram-only`) that detects RAM
and nothing else, so keyboards, fans and the rest are never touched. Set `IGNITION_RGB_DEVICES` to pick the
devices by name, and `IGNITION_IDLE_COLOR` (e.g. `ffffff`) for RAM that doesn't read back its own colour
(HyperX Fury does not, and would otherwise be restored dark):

```sh
  --hook-burn 'IGNITION_COLOR=ff0000 /usr/local/lib/ignition/openrgb-ram burn' \
  --hook-idle 'IGNITION_IDLE_COLOR=ffffff /usr/local/lib/ignition/openrgb-ram idle'
```

### How the lockout works on computers

Each computer runs `ignition-enforcer` as root. Every few seconds it sends a heartbeat to the server, which
replies with the current session and the shared block list. The Ignite page shows each enforcer as
*ready*, *locked* or *offline*. While a burn is active (not during a T-minus) it:

- points **blocked sites** at `0.0.0.0` in `/etc/hosts`, along with common subdomains and sister domains. The
  installer turns off DNS-over-HTTPS in Firefox, Zen, Chrome, Chromium and Brave through their policy
  files, because DoH would route around it;
- kills **blocked programs** and their children if you start them during the burn. One that's already open at
  liftoff is **paused** instead (`SIGSTOP`, its windows minimized on KDE Plasma) and resumed when the burn
  ends, so a game picks up exactly where it was. Resuming it yourself with `kill -CONT` lasts under a
  second. FUSE servers in the tree (a game's packed filesystem) keep running so nothing touching their mount
  hangs. A T-minus sends a desktop notification a minute before liftoff naming what it will pause;
- closes any **`--block-ports`** to everyone, local and remote, including published container ports;
- refuses loopback TCP to the **T3 ports** from every process outside the T3 server's own cgroups, so agents
  keep running but you can't open a client, and kills connections already open (`ss -K`). It also stops
  those cgroups reaching Cloudflare's tunnel port (7844; `t3.egress_ports` in the config), since T3
  Connect's `cloudflared` runs inside the server's cgroup and would otherwise let remote clients in;
- runs the **burn/idle hooks**.

The session and block list are cached in `/var/lib/ignition/state.json` (paused processes in
`frozen.json`), so blocks hold until the end time even if the server is unreachable. Stopping the enforcer
cleanly lifts everything and resumes what it paused:

```sh
sudo systemctl stop ignition-enforcer
```

## KDE Plasma widgets

Three widgets for Plasma 6, in the same Nothing-style dot-matrix as the Android ones. Each works on the desktop
or in a panel, where it shrinks to an icon and opens as a popup:

- **Ignition**: the dial. Idle it's a flame; click it to arm a burn (3, 2, 1, click again to scrub), with the
  burn length and T-minus set by − / + or the scroll wheel. During a T-minus: *Ignite now*, and *Scrub* until
  it locks an hour out. While burning: a ring of dots burning down, +5/+15/+30, and *Hold to end* for burns
  over an hour. Also today's goal, the next scheduled launch (with *Skip*) and each computer's blocker. In a
  panel, the ring and the minutes left.
- **Next up**: the top of the Need list in the website's order. Click a dot to check it off, or *+ Add* one.
  In a panel, how many are left, with an accent dot when something's due.
- **Burn goals**: today and this week against your goals, and the last seven days as columns of dots. In a
  panel, a ring for today's goal.

`install.sh` and `update.sh` install them when they find Plasma 6 (`kpackagetool6`); on its own,
`plasma/install.sh [--server URL]`. They talk to the app on this computer, or else the one its blocker follows;
change it in each widget's settings, which also pick its accent: Nothing's red, the accent colour from System
Settings, or any colour. They use Nothing's *Ndot 57* and *Lettera Mono* fonts when installed, and
a plain monospace otherwise. Plasma loads updated widgets when it restarts:
`systemctl --user restart plasma-plasmashell`.

## Android app

- **The blocker** is an accessibility service (Settings → Phone → *App & site blocker*). It only reads the
  foreground app and, in browsers, the address bar. Nothing is logged or leaves the phone. Any app that
  opens web links counts as a browser. Firefox and its forks, Chromium browsers (Chrome, Brave, Edge,
  Vivaldi...), Samsung Internet, DuckDuckGo and Opera are read by their address bar's id; anything else
  falls back to the first filled-in text field outside a list. **Every game** catches anything Android
  files as a game, even in a hidden folder or the private space.
- **Notifications:** an ongoing countdown while a T-minus or burn runs, with *Ignite now*, *Scrub* and
  *+15 min*. *Liftoff*, *main engine cutoff* and T-minus warnings show on the lock screen.
- **Ring at liftoff** (optional): a scheduled T-minus can ring the phone and wake the lock screen, so you
  can't sleep through the start of a session. By default it buzzes and plays a few quiet seconds of rocket
  launch (NASA's SLS engine test fire); pick *Buzz* for vibration only or *Ringtone* for your phone's
  ringtone, which rings until you tap. *Test* plays it.
- **Do Not Disturb while burning** (optional). Switched back afterwards, but only if you haven't touched it
  yourself.
- **Launch window:** a nudge, not a lock. Pick a daily stretch (say weekday afternoons). If you've been home
  a while and haven't started anything, it asks whether you want to light a T-minus. The phone recognises
  home from its Wi-Fi, with no location permission, and nothing leaves the device.
- **Widgets** (Nothing-style, dot-matrix): *Ignite* (1×1), *Next up* (2×2), *Burn goals* (2×1).
- **Sync:** Settings → Sync → the server's address. Everything merges both ways, and the newest edit wins.
  A burn started on the phone reaches the enforcers within a second. One started elsewhere shows up when
  you open the app, or within about 15 minutes in the background.

### Tasker / automation

The app broadcasts these intents. Catch them with Tasker's **Intent Received** event:

| Action | When |
| --- | --- |
| `ca.heeney.ignition.COUNTDOWN` | a T-minus countdown starts |
| `ca.heeney.ignition.LIFTOFF` | a burn starts |
| `ca.heeney.ignition.MECO` | a burn ends (naturally or early) |

Extras: `minutes`, `starts_at`, `ends_at` (unix seconds), `source` (`manual` or `schedule`).

To drive ignition *from* Tasker, use **Send Intent** → *Broadcast Receiver*, package `ca.heeney.ignition`:

| Action | Extras |
| --- | --- |
| `ca.heeney.ignition.IGNITE` | `minutes` (default 50), `delay` (T-minus minutes, default 0) |
| `ca.heeney.ignition.SCRUB` | none |
| `ca.heeney.ignition.EXTEND` | `minutes` (default 15) |

### Building

Each commit is its own build: the `versionCode` is the number of commits on `main` and the version is
`<ignitionVersion>.<that number>` (e.g. `2.1.12`), so a build of any newer commit installs over an older one.

`android/autobuild.sh install`, on the build machine, sets up a user timer that checks GitHub every minute
and publishes a release tagged `v<version>` for every commit on `main` that changes `android/` or `web/`.
Obtainium then picks it up. It needs the toolchain and signing key below, and `gh` logged in.

Needs JDK 17, Gradle and Android SDK 35. `android/build.sh` expects them in `~/.local/android-toolchain`
and signs releases with `~/.config/ignition/release.jks` (via `keystore.properties`).

```
android/build.sh     # → android/app/build/outputs/apk/release/app-release.apk
android/install.sh   # adb if wireless debugging is connected, else Termux + installer prompt
```

The UI is `../web`, bundled as assets. The app answers the same `/api/...` calls itself through a JS bridge
(`LocalApi.kt`, mirroring `server/ignition.py`). JVM tests, including a live sync round trip:

```
IGNITION_TEST_SERVER=http://127.0.0.1:4597 gradle testDebugUnitTest
```

## API

`GET/POST /api/items`, `PATCH/DELETE /api/items/<id>`, `GET /api/ignite`, `POST /api/ignite` (`minutes`,
`delay`), `POST /api/ignite/{stop,scrub,now,extend,skip}`, `GET/PUT /api/settings` (goals, schedules,
block, launch_window), `GET /api/stats`, `POST /api/sync`, `GET /api/ping`,
`POST /api/enforcer/heartbeat`.

There's no authentication. Keep the server on localhost and your tailnet, which is what the installer does.
