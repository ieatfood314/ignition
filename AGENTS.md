# Notes for coding agents

Start with `docs/advanced.md`. Its "Pieces" section maps the repo, "Building" covers the Android build and tests, and "API" lists the endpoints. The README is the guide for people installing it.

## Where things live
- `server/ignition.py` is the whole backend in one file. It covers:
  - the SQLite `Store` and the `clean_*` input checks;
  - the burn, scrub and early-stop rules (`App`, with the constants at the top);
  - the HTTP handler (`make_handler`) and the launch `scheduler`.
- `server/screen/` is screen time and limits (from lightcurve, merged in): `core.py` the record and reports, `limits.py` the limit rules and unlocks, `hub.py` their endpoints (mounted by `ignition.py`), `tracker.py` the Plasma tracker. Its data is a separate SQLite file, `<db>.screen.db`.
- `web/` is the only UI, used by both the server and the Android app.
  - `app.js` (about 88 KB) and `style.css` (about 55 KB) are large.
  - `app.js` is divided by `/* ---- name ---- */` banners. Grep for the section you need and read that range, not the whole file.
  - `screen.js` is the Screen and Limits tabs and the Devices settings page.
- `android/app/src/main/kotlin/ca/heeney/ignition/`:
  - `LocalApi.kt` answers the same `/api/...` calls as the server, through the `IgnitionNative` JS bridge set up in `MainActivity.kt`.
  - `Logic.kt` holds the burn rules, `Sync.kt` syncs with a server, and `BlockerService.kt` and `Blocking.kt` block things on the phone (burns, limits, and the guard over ignition's own settings).
  - `ScreenTime.kt` reads the usage log and sends spans (`ScreenSync`), and keeps the server's limits (`LimitStore`). `Limits.kt`, `Spans.kt` and `Sites.kt` are its pure logic. `LocalLimits.kt` keeps burn-only limits on a standalone phone and uploads them when it connects. Screen time and server-limit pages call the server through `requestAsync` (see `REMOTE` in app.js); burns, settings and standalone burn limits are answered by `LocalApi`.
- `enforcer/ignition-enforcer.py` is the root daemon on each computer. It applies blocks (hosts file, closing programs, browser policies) and reports to the server.
- `plasma/` holds the KDE Plasma 6 widgets (QML), which use the server's API. Their shared parts are in `plasma/shared/`.
- `install.sh`, `update.sh` and `uninstall.sh` are what users run. topgrade runs `update.sh`.

## Things that bite
- **The rules are written twice.** A change to the burn rules or an API endpoint in `server/ignition.py` usually needs the same change in `LocalApi.kt` and `Logic.kt`. Otherwise the phone and the computers disagree, and sync carries the disagreement both ways. Limit rules likewise live in both `server/screen/limits.py` and `Limits.kt`; `tests/test_limits.py` and `LimitsTest.kt` share cases.
- **Limits must hold on every device.** Computers get them through the heartbeat's `limit_block`, the phone through `GET /api/limits?device=…&app=ignition`. A loosening has to go through the wait in `limits.py`, never around it.
- **Every commit on `main` that touches `android/` or `web/` becomes a public release.** The autobuild publishes it and Obtainium installs it on the phone. Don't bump versions or tag by hand; the version is the commit count.
- **Test servers can touch real data.** A machine running ignition already has a live server on 4580, using the default database. Start a test server with its own `--port` and `--db`, for example `python3 -I server/ignition.py --port 4597 --db /tmp/ign-test.db`.
- **Getting changes live:** the server reads `web/` on every request, so web changes are live as soon as they're merged. Changes to `server/` need `ignition.service` (a user unit running from `~/src/ignition`) restarted.
- **regolith and mars update from GitHub main.** `update.sh` brings them enforcer and widget changes and overwrites anything hand-copied.
- **Tests:**
  - JVM tests are in `android/app/src/test`. The "Building" section of `docs/advanced.md` shows how to run them against a live server.
  - Screen time and limits have server tests: `python3 -m unittest discover -s tests -t .`. The rest of the server has none; check changes against a test server.

## Shipping
Aidan is the only user. Merge finished changes straight into `main` and push. Don't open a PR or add compatibility shims.
