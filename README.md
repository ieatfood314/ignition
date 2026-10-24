# ignition

A focus app for people who don't trust themselves.

Most screen-time tools fail the same way: the block is one tap from gone. A "skip for today", a PIN you
set yourself, a toggle in Settings. The moment you actually want to cave, you can — which is the only
moment that counts. Ignition is built the other way round. When a burn is running there is **no easy out**,
by design. That's the whole point, and if that sounds like too much, it probably isn't the app for you.

Two lists and a button:

- **Need to do** — title, time estimate, due date, priority, notes.
- **Want to do** — the someday pile: title, estimate, priority, age, notes.
- **Ignite** — set the minutes, hold the button, and you're locked in.

### What "locked in" means

- A burn of **an hour or less cannot be ended early**. No stop button, no scrub, nothing. It runs out
  when it runs out.
- Longer burns get a hold-to-end button, because occasionally a two-hour block really does need to die.
- Extending a burn never changes which rule applies — you can always add time, never remove it.
- **T-minus:** start a burn on a delay (a break first). You can scrub or ignite early *before* it lifts
  off; once it's burning, the rules above take over.
- **Liftoff** and **main engine cutoff** bracket each burn. At cutoff you can light a **second stage** to
  keep going.

While a burn is active, the blocks come down everywhere at once.

## What it blocks

Set this in **Settings → Blocking**; it syncs to every device.

- **Websites** — by domain, subdomains included. On computers this is enforced in `/etc/hosts` (and the
  installer turns off DNS-over-HTTPS in Firefox, Zen and Chrome, since DoH would route around it). On the
  phone it's enforced by watching the browser's address bar.
- **Phone apps** — pick them from a list, or flip on **every game** to catch anything Android files as a
  game, hidden folder and private space included. Open one during a burn and you're bounced to the home
  screen.
- **Local services** — a self-hosted media server, say. The enforcer closes the port to everything,
  including already-open connections, for as long as the burn lasts.
- **Programs on computers** — listed by name (`topgrade`, `lutris`, …). Started during a burn, they're
  killed, along with whatever they spawned.
- **T3 Code** — loopback access to the T3 port is refused for every process outside the T3 server's own
  cgroups, so agents keep running but you can't open a client.

### Launch window

A nudge, not a lock. Pick a daily stretch (say weekday afternoons); if you've been home a while — the
phone recognises home from its Wi-Fi, no location permission, nothing leaves the device — and you haven't
started anything, it asks whether you want to light a T-minus. For the 3:15-to-5:30-on-your-phone problem.

### Extras

- **Scheduled launches** that ignite on their own at set times, with a T-minus warning first.
- **Daily and weekly burn goals**, with progress on the timer page.
- **Ring at liftoff** — a scheduled T-minus can ring your phone and wake the lock screen, so you can't
  sleep through the start of a session.
- On a desktop running the enforcer, a burn can **recolour your RAM** to ignition's accent and put it back
  at cutoff (an OpenRGB hook).

## Pieces

There are two independent halves that share a sync protocol.

1. **Server + website** — Python standard library, no build step. Runs on one machine on a tailnet. Root
   **enforcers** on each computer apply the blocks.
2. **Android app** (`android/`) — works entirely on its own: no account, no server, no Tailscale. Point it
   at a server in Settings and everything syncs both ways.

```
server/ignition.py              web app + JSON API + SQLite + launch scheduler (user service)
web/                            the frontend (also bundled into the Android app)
enforcer/ignition-enforcer.py   root daemon on each computer that applies the blocks
enforcer/openrgb-ram            optional burn/idle RGB hook
enforcer/install.sh             installs the enforcer as a system service
install.sh                      installs the server as a user service + Tailscale Serve
android/                        the Android app (Kotlin, no dependencies)
```

## Android app

Grab the APK from [Releases](../../releases), or point [Obtainium](https://obtainium.imranr.dev/) at this
repo. Then:

- **Standalone:** lists, burns, schedules, goals and your block list all live on the phone.
- **The blocker** needs accessibility access (Settings → Phone → *App & site blocker*). It only reads the
  foreground app and, in browsers, the address bar; nothing is logged or leaves the phone. Browsers
  covered: Firefox, Iceraven, Tor Browser and Chrome.
- **Notifications:** an ongoing countdown while a T-minus or burn runs, with *Ignite now*, *Scrub* and
  *+15 min*. *Liftoff*, *main engine cutoff* and T-minus warnings show on the lock screen.
- **Do Not Disturb while burning** (optional). Switched back afterwards, but only if you haven't touched
  it yourself.
- **Widgets** (Nothing-style, dot-matrix): *Ignite* (1×1), *Next up* (2×2), *Burn goals* (2×1).
- **Sync (optional):** Settings → Sync → the server's address (e.g. `http://my-server:8445`). Everything
  merges both ways, newest edit wins. A burn started on the phone reaches the enforcers within a second;
  one started elsewhere shows up when you open the app, or within ~15 minutes in the background.

### Tasker / automation

The app broadcasts these intents — catch them with Tasker's **Intent Received** event:

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
| `ca.heeney.ignition.SCRUB` | — |
| `ca.heeney.ignition.EXTEND` | `minutes` (default 15) |

### Building

Self-contained toolchain in `~/.local/android-toolchain` (JDK 17, Gradle, SDK 35). Releases are signed
with `~/.config/ignition/release.jks` (via `keystore.properties`).

```
android/build.sh     # → android/app/build/outputs/apk/release/app-release.apk
android/install.sh   # adb if wireless debugging is connected, else Termux + installer prompt
```

The UI is `../web`, bundled as assets; the app answers the same `/api/...` calls itself through a JS
bridge (`LocalApi.kt`, mirroring `server/ignition.py`). JVM tests, including a live sync round trip:

```
IGNITION_TEST_SERVER=http://127.0.0.1:4597 gradle testDebugUnitTest
```

## How the lockout works on computers

Each machine runs `ignition-enforcer` as root. Every few seconds it heartbeats to the server, which
replies with the current session and the shared block list; the timer page shows each enforcer as *ready*,
*locked* or *offline*. While a burn is active (not during a T-minus) it:

- rejects loopback TCP to the **T3 port** from every process outside the T3 server's cgroups, and kills
  connections already open (`ss -K`);
- closes any **`--block-ports`** to everyone, local and remote, including published container ports;
- points **blocked sites** at `0.0.0.0` in `/etc/hosts`;
- kills **blocked programs** and their children on sight;
- runs the **burn/idle hooks** (e.g. the RGB recolour).

The session and block list are cached in `/var/lib/ignition/state.json`, so blocks hold until the end time
even if the server is unreachable. Stopping the enforcer cleanly lifts everything.

## Server install

On the host, as your user:

```sh
./install.sh
```

Serves the app on `127.0.0.1:4580` and on `http://<host>:8445` via Tailscale Serve (override with
`IGNITION_PORT` / `IGNITION_TAILNET_PORT`). Plain HTTP, tailnet only.

Enforcer, on each computer to lock down, as root:

```sh
sudo enforcer/install.sh --server-url http://127.0.0.1:4580 --t3-user me --host nas \
  --t3-ports 3773,13773 --block-ports 8000
sudo enforcer/install.sh --server-url http://nas.tail-net.ts.net:8445 --t3-user me --host desk \
  --block topgrade,lutris --notify \
  --hook-burn '/usr/local/lib/ignition/openrgb-ram burn' \
  --hook-idle '/usr/local/lib/ignition/openrgb-ram idle'
```

Re-running keeps options you don't pass. Config lives in `/etc/ignition/enforcer.json`; edit it and
`systemctl restart ignition-enforcer` to change it by hand.

### API

`GET/POST /api/items`, `PATCH/DELETE /api/items/<id>`, `GET /api/ignite`, `POST /api/ignite` (`minutes`,
`delay`), `POST /api/ignite/{stop,scrub,now,extend,skip}`, `GET/PUT /api/settings` (goals, schedules,
block, launch_window), `GET /api/stats`, `POST /api/sync`, `GET /api/ping`,
`POST /api/enforcer/heartbeat`.

## Emergency exit

This is for self-control, not security — there's no threat model here but you. If something is genuinely
on fire:

```sh
sudo systemctl stop ignition-enforcer
```

That's the escape hatch. Using it to check Reddit is between you and you.
