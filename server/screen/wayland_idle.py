"""Idle detection on Wayland, through ext-idle-notify-v1, with no dependencies.

The compositor tells us when there has been no keyboard or pointer input for a
while, and again when there is. This speaks just enough of the Wayland wire
protocol to ask for that: bind the seat and the idle notifier, request one
notification, then listen.
"""

import os
import socket
import struct
import threading
import time


class Unsupported(Exception):
    pass


def _string(s):
    raw = s.encode() + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _message(obj, opcode, payload=b""):
    return struct.pack("<IHH", obj, opcode, 8 + len(payload)) + payload


class IdleWatch(threading.Thread):
    """Watches for stretches without keyboard or pointer input.

    `watches` is a list of (seconds, raw, callback). The callback gets True
    when that long has passed without input and False when input returns.
    A normal watch stays quiet while something (a video, say) is asking the
    compositor to keep the screen awake; a raw one ignores that and needs
    version 2 of the protocol, so it is skipped on compositors without it.

    Callbacks run on this thread; hand off to your main loop inside them.
    Reconnects on its own if the compositor restarts.
    """

    def __init__(self, watches, log=print):
        super().__init__(daemon=True, name="idle-watch")
        self.watches, self.log = list(watches), log
        self.supported = None  # None until the first connection settles it

    def _all_awake(self):
        for _, _, callback in self.watches:
            callback(False)

    def run(self):
        warned = False
        while True:
            try:
                self._session()
            except Unsupported as e:
                self.supported = False
                self.log(f"idle detection unavailable: {e}")
                return
            except OSError as e:
                if not warned:
                    self.log(f"idle watch lost the compositor ({e}); retrying")
                    warned = True
            # whatever happened, nobody is known to be idle any more
            self._all_awake()
            time.sleep(5)

    def _session(self):
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        display = os.environ.get("WAYLAND_DISPLAY") or _find_display(runtime)
        path = display if display.startswith("/") else os.path.join(runtime, display)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(path)
            REGISTRY, SYNC, SEAT, NOTIFIER, FIRST_NOTE = 2, 3, 4, 5, 6
            sock.sendall(_message(1, 1, struct.pack("<I", REGISTRY)) + _message(1, 0, struct.pack("<I", SYNC)))
            globals_, buf, notes = {}, b"", None
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    raise OSError("connection closed")
                buf += chunk
                while len(buf) >= 8:
                    obj, opcode, size = struct.unpack_from("<IHH", buf)
                    if size < 8:
                        raise OSError("malformed message")
                    if len(buf) < size:
                        break
                    body, buf = buf[8:size], buf[size:]
                    if obj == 1 and opcode == 0:  # wl_display.error
                        code = struct.unpack_from("<II", body)[1]
                        n = struct.unpack_from("<I", body, 8)[0]
                        raise OSError(f"protocol error {code}: {body[12:12 + n - 1].decode(errors='replace')}")
                    if obj == REGISTRY and opcode == 0:  # global
                        name, n = struct.unpack_from("<II", body)
                        iface = body[8:8 + n - 1].decode()
                        version = struct.unpack_from("<I", body, 8 + n + (-n % 4))[0]
                        globals_.setdefault(iface, (name, version))
                    elif obj == SYNC and opcode == 0 and notes is None:  # every global has been announced
                        if "ext_idle_notifier_v1" not in globals_ or "wl_seat" not in globals_:
                            raise Unsupported("this compositor has no ext-idle-notify-v1")
                        version = min(globals_["ext_idle_notifier_v1"][1], 2)
                        out = b""
                        for iface, new_id, v in (("wl_seat", SEAT, 1), ("ext_idle_notifier_v1", NOTIFIER, version)):
                            out += _message(REGISTRY, 0, struct.pack("<I", globals_[iface][0]) + _string(iface)
                                            + struct.pack("<II", v, new_id))
                        notes = {}
                        for seconds, raw, callback in self.watches:
                            if raw and version < 2:
                                continue
                            note = FIRST_NOTE + len(notes)
                            # 1 = get_idle_notification, 2 = get_input_idle_notification
                            out += _message(NOTIFIER, 2 if raw else 1,
                                            struct.pack("<III", note, int(seconds * 1000), SEAT))
                            notes[note] = callback
                        sock.sendall(out)
                        self.supported = True
                    elif notes and obj in notes and opcode in (0, 1):  # idled, resumed
                        notes[obj](opcode == 0)
        finally:
            sock.close()


def _find_display(runtime):
    """A service started before the session exports WAYLAND_DISPLAY still has to find the socket."""
    try:
        names = sorted(n for n in os.listdir(runtime) if n.startswith("wayland-") and not n.endswith(".lock"))
    except OSError:
        names = []
    return names[0] if names else "wayland-0"
