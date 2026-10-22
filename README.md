# ignition

A small productivity app for my tailnet: a list of things I **need** to do, a list of things I
**want** to do, and an **ignite** button that locks me out of distractions for a set number of minutes.

- **Need to do:** title (the only required field), time estimate, due date, priority, and notes.
- **Want to do:** title, time estimate, priority, how long it has been on the list, and notes.
- **Ignite:** type a number of minutes and press the big button. Until the timer runs out:
  - T3 Code clients across the tailnet are cut off. Threads that are already running keep running,
    but no client can start new threads or watch existing ones.
  - Lutris can't be launched on the gaming PC. Games that are already running are left alone.
  - Sessions of an hour or less can't be ended early. Longer ones have a hold-to-end button.

Everything is Python stdlib plus vanilla JS. There are no dependencies and no build step.

## Layout

```
server/ignition.py              web app + JSON API + SQLite (runs as a user service)
web/                            the frontend (static files)
enforcer/ignition-enforcer.py   root daemon on each machine that applies the blocks
enforcer/install.sh             installs the enforcer as a system service
install.sh                      installs the server as a user service + Tailscale Serve
```

## How the lockout works

Each machine runs `ignition-enforcer` as root. Every 5 seconds it sends a heartbeat to the server,
which replies with the current session. The timer page shows each enforcer as *ready*, *locked*,
or *offline*. While a session is active:

- **T3 Code**: an nftables table (`inet ignition`) rejects loopback TCP to the T3 port from every
  process outside the T3 server's own cgroups (`app.slice/t3code.service` and
  `app.slice/app-t3code.slice`, where agent sessions run). Tailscale Serve, SSH tunnels from the
  desktop app, and local clients all get refused. Connections that are already open get killed
  with `ss -K`. The server and its agents never notice.
- **Programs** listed with `--block` are SIGKILLed if they start after the session began. The
  check runs once a second. Desktop notifications are optional.

The session is cached in `/var/lib/ignition/state.json`, so the blocks hold until the end time
even if the server can't be reached. Stopping the enforcer cleanly removes its nftables table.

## Install

Server (on the host machine, as your user):

```sh
./install.sh
```

This serves the app on `127.0.0.1:4580` and on `http://<host>:8445` via
Tailscale Serve. Set `IGNITION_PORT` or `IGNITION_TAILNET_PORT` to change the ports. It is plain HTTP; only tailnet devices can reach it.

Enforcer (on each machine to lock down, as root):

```sh
sudo enforcer/install.sh --server-url http://127.0.0.1:4580 --t3-user me --host nas --t3-ports 3773,13773
sudo enforcer/install.sh --server-url http://<host>.<tailnet>.ts.net:8445 --t3-user me --host gamer --block lutris --notify
```

Config ends up in `/etc/ignition/enforcer.json`. Edit it and run
`systemctl restart ignition-enforcer` to change it later.

## Emergency exit

This tool is for self-control, not security. If something is actually on fire:

```sh
sudo systemctl stop ignition-enforcer
```
