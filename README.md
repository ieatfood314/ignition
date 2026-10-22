# ignition

A small productivity app: a list of things I **need** to do, a list of things I **want** to do, and an
**ignite** button that locks me out of distractions for a set number of minutes.

- **Need to do:** title (the only required field), time estimate, due date, priority, notes.
- **Want to do:** title, time estimate, priority, how long it's been on the list, notes.
- **Sorting:** smart (due date, then priority), due date, quickest first, priority, oldest, newest, A–Z.
- **Ignite:** punch in the minutes and press the big button.
  - **T-minus:** start the burn later (a set break first). You can scrub the launch or ignite early.
  - **Extend** a burn while it's going (+5 / +15 / +30).
  - **No escape:** burns of an hour or less can't be ended early. Longer ones get a hold-to-end button.
    Extending doesn't change which rule applies.
  - **Launch schedule:** ignites by itself at set times on set days, with a T-minus warning first. You
    can skip a single day.
  - **Burn goals:** daily and weekly targets, with progress shown on the timer page.

There are two separate pieces that share a sync protocol:

1. **The server + website** (Python stdlib, no build step). It runs on one machine on a tailnet. Root
   **enforcers** on each machine lock things down while a burn is active.
2. **The Android app** (`android/`). It works completely on its own, with no account, server or
   Tailscale. If you have a server, point the app at it in Settings and everything syncs both ways.

## Layout

```
server/ignition.py              web app + JSON API + SQLite + launch scheduler (user service)
web/                            the frontend (also bundled into the Android app)
enforcer/ignition-enforcer.py   root daemon on each machine that applies the blocks
enforcer/install.sh             installs the enforcer as a system service
install.sh                      installs the server as a user service + Tailscale Serve
android/                        the Android app (Kotlin, no dependencies)
```

## Android app

Grab the APK from [Releases](../../releases), or point [Obtainium](https://obtainium.imranr.dev/) at this
repo. Then:

- **Standalone:** your lists, burns, schedules and goals live on the phone.
- **Notifications:** an ongoing countdown while a T-minus or burn is running, with *Ignite now*, *Scrub* and
  *+15 min* buttons. You also get *Liftoff* and *MECO* (main engine cutoff) alerts, and a T-minus warning
  before scheduled launches.
- **Do Not Disturb while burning** (optional, in Settings). It's switched back off afterwards, but only if
  you haven't changed it yourself.
- **Widgets**, small and in Nothing style (dot-matrix, follows the system light/dark theme):
  - *Ignite* (1×1): a dot ring that burns down with the minutes left, a T-minus count, or the flame.
  - *Next up* (2×2): the top of the need-to-do list.
  - *Burn goals* (2×1): today and this week against your goals.
- **Sync (optional):** Settings → Sync → the server's address (for example `http://my-server:8445`). Lists,
  burns, schedules and goals sync both ways, with the newest edit winning. A burn started on the phone reaches
  the server, and so the enforcers, within a second. A burn started elsewhere shows up when you open the app,
  or within about 15 minutes in the background.

### Tasker / automation

The app broadcasts these intents. Catch them with Tasker's **Intent Received** event to flip Focus mode,
Bedtime, ringer, or anything else:

| Action | When |
| --- | --- |
| `ca.heeney.ignition.COUNTDOWN` | a T-minus countdown starts |
| `ca.heeney.ignition.LIFTOFF` | a burn starts |
| `ca.heeney.ignition.MECO` | a burn ends (naturally or early) |

Extras: `minutes`, `starts_at`, `ends_at` (unix seconds), `source` (`manual` or `schedule`).

To control ignition *from* Tasker, use **Send Intent** with target *Broadcast Receiver* and package
`ca.heeney.ignition`:

| Action | Extras |
| --- | --- |
| `ca.heeney.ignition.IGNITE` | `minutes` (default 50), `delay` (T-minus minutes, default 0) |
| `ca.heeney.ignition.SCRUB` | — |
| `ca.heeney.ignition.EXTEND` | `minutes` (default 15) |

### Building

The app uses a self-contained toolchain in `~/.local/android-toolchain` (JDK 17, Gradle, SDK 35). Releases
are signed with `~/.config/ignition/release.jks`, which `keystore.properties` points to.

```
android/build.sh     # → android/app/build/outputs/apk/release/app-release.apk
android/install.sh   # adb if wireless debugging is connected, else Termux + installer prompt
```

The UI is `../web`, bundled as assets. The app answers the same `/api/...` calls itself through a JS bridge
(`LocalApi.kt`, which mirrors `server/ignition.py`). JVM tests, including a live sync round trip against a
throwaway server:

```
IGNITION_TEST_SERVER=http://127.0.0.1:4597 gradle testDebugUnitTest
```

Debug hook: `adb shell am start -n ca.heeney.ignition/.MainActivity --ez render_widgets true` writes widget
PNGs to `Android/data/ca.heeney.ignition/files/`.

## How the lockout works

Each machine runs `ignition-enforcer` as root. Every 5 seconds it sends a heartbeat to the server, which
replies with the current session. The timer page shows each enforcer as *ready*, *locked*, or *offline*.
While a burn is active (not during a T-minus):

- **T3 Code:** an nftables table (`inet ignition`) rejects loopback TCP to the T3 port from every process
  outside the T3 server's own cgroups (`app.slice/t3code.service` and `app.slice/app-t3code.slice`, where
  agent sessions run). Tailscale Serve, SSH tunnels from the desktop app, and local clients are all
  refused, and connections that are already open are killed with `ss -K`. The server and its agents never
  notice.
- **Programs** listed with `--block` are SIGKILLed if they start after the burn began. The check runs once a
  second, and desktop notifications are optional.

The session is cached in `/var/lib/ignition/state.json`, so blocks hold until the end time even if the
server can't be reached. Stopping the enforcer cleanly removes its nftables table.

## Server install

On the host machine, as your user:

```sh
./install.sh
```

This serves the app on `127.0.0.1:4580` and on `http://<host>:8445` via Tailscale Serve. Set
`IGNITION_PORT` or `IGNITION_TAILNET_PORT` to change the ports. It is plain HTTP, and only tailnet devices
can reach it.

Enforcer, on each machine to lock down, as root:

```sh
sudo enforcer/install.sh --server-url http://127.0.0.1:4580 --t3-user me --host nas --t3-ports 3773,13773
sudo enforcer/install.sh --server-url http://<host>.<tailnet>.ts.net:8445 --t3-user me --host gamer --block lutris --notify
```

The config ends up in `/etc/ignition/enforcer.json`. To change it later, edit it and run
`systemctl restart ignition-enforcer`.

### API

`GET/POST /api/items`, `PATCH/DELETE /api/items/<id>`, `GET /api/ignite`, `POST /api/ignite` (`minutes`,
`delay`), `POST /api/ignite/{stop,scrub,now,extend,skip}`, `GET/PUT /api/settings`, `GET /api/stats`,
`POST /api/sync` (`since`, `items`, `sessions`, `settings`; rows merge last-writer-wins on `updated_at`),
`GET /api/ping`.

## Emergency exit

This is for self-control, not security. If something is actually on fire:

```sh
sudo systemctl stop ignition-enforcer
```
