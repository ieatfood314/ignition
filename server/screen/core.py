"""Storage and analysis shared by the hub, the tracker and the CLI.

Everything is built on spans: (device, start, end, app, site), in unix
milliseconds. A span is one unbroken stretch of one thing in front of you on
one device. Nothing is pre-aggregated, so every number the reports give can be
traced back to the spans it came from.
"""

import datetime as dt
import json
import os
import re
import secrets
import sqlite3
import statistics
import threading
import time

VERSION = "1.1.0"

DAY_START_HOUR = 4  # a day runs 04:00 to 04:00, so a late night stays with the day it belongs to
BIN = 600  # seconds per light-curve point
SESSION_GAP = 45  # the same thing again within this many seconds is the same session
SITTING_GAP = 300  # a break shorter than this doesn't end a sitting
BASELINE_DAYS = 28
MIN_BASELINE = 5  # fewer days of history than this and "usual" isn't claimed

CATEGORIES = ("make", "talk", "other", "browse", "watch", "play")  # good to bad
CATEGORY_DEFAULTS = (
    ("make", "Make", "#ffffff", "Writing, building, coding"),
    ("talk", "Talk", "#a3a3a3", "Messages, mail, calls"),
    ("other", "Other", "#575757", "Settings, files, home screen"),
    ("browse", "Browse", "#6e2219", "Other web pages"),
    ("watch", "Watch", "#a92e1f", "Video, feeds, reading"),
    ("play", "Play", "#e23a29", "Games"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'desktop',
    platform TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    synced_to INTEGER NOT NULL DEFAULT 0,
    active_at_sync INTEGER NOT NULL DEFAULT 0,
    renamed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS spans (
    device TEXT NOT NULL,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    app TEXT NOT NULL,
    site TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (device, start)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS spans_end ON spans (end);
CREATE TABLE IF NOT EXISTS pickups (
    device TEXT NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY (device, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS names (
    app TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    hint TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS categories (
    key TEXT PRIMARY KEY,
    cat TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS category_defs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    about TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL,
    custom INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS legacy (
    device TEXT NOT NULL,
    date TEXT NOT NULL,
    app TEXT NOT NULL,
    seconds REAL NOT NULL,
    PRIMARY KEY (device, date, app)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS outbox (
    start INTEGER PRIMARY KEY
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS unlocks (
    id TEXT PRIMARY KEY,
    limit_id TEXT NOT NULL,
    device TEXT NOT NULL DEFAULT '',
    started INTEGER NOT NULL,
    ready INTEGER NOT NULL,
    at INTEGER,
    until INTEGER
);
CREATE INDEX IF NOT EXISTS unlocks_at ON unlocks (at);
CREATE TABLE IF NOT EXISTS limits (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    pending TEXT,
    pending_at INTEGER,
    created INTEGER NOT NULL,
    updated INTEGER NOT NULL
);
"""


def data_dir(app="ignition"):
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), app)


def config_dir(app="ignition"):
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), app)


def open_db(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False, timeout=15)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(SCHEMA)
    db.executemany("INSERT OR IGNORE INTO category_defs (id, name, color, about, position, custom) "
                   "VALUES (?, ?, ?, ?, ?, 0)",
                   [(id_, name, color, about, i) for i, (id_, name, color, about) in enumerate(CATEGORY_DEFAULTS)])
    db.commit()
    return db


def kv_get(db, key, default=None):
    row = db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def kv_set(db, key, value):
    db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, str(value)))


# ---- categories ----

_CATEGORY_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def category_list(db):
    """Every category in chart order. The six defaults can be renamed and recoloured, but not deleted."""
    return [{"id": id_, "name": name, "color": color, "about": about, "custom": bool(custom)}
            for id_, name, color, about, custom in db.execute(
                "SELECT id, name, color, about, custom FROM category_defs ORDER BY position, id")]


def category_ids(db):
    return [c["id"] for c in category_list(db)]


def save_categories(db, rows):
    """Replace the editable category list, creating ids for new rows and removing omitted custom rows.

    A whole list arrives together so a batch of edits is atomic. Deleting a category targeted by a current
    or pending limit would loosen that limit, so it is refused instead of slipping around the usual wait.
    """
    if not isinstance(rows, list) or not rows or len(rows) > 32:
        raise ValueError("Keep between 1 and 32 categories.")
    current = {c["id"]: c for c in category_list(db)}
    seen, names, cleaned = set(), set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each category needs a name and colour.")
        id_ = str(row.get("id") or "")
        if id_ and id_ not in current:
            raise ValueError("That category no longer exists. Reload and try again.")
        name = str(row.get("name") or "").strip()[:32]
        color = str(row.get("color") or "").lower()
        if not name:
            raise ValueError("Every category needs a name.")
        if not _CATEGORY_COLOR.match(color):
            raise ValueError("Colours need six hex digits, like #e23a29.")
        folded = name.casefold()
        if folded in names:
            raise ValueError("Category names must be different.")
        if id_ and id_ in seen:
            raise ValueError("A category appears twice.")
        if id_:
            seen.add(id_)
        names.add(folded)
        cleaned.append({"id": id_, "name": name, "color": color})
    missing = {id_ for id_, c in current.items() if c["custom"] and id_ not in seen}
    required = {id_ for id_, c in current.items() if not c["custom"]}
    if not required <= seen:
        raise ValueError("The default categories can't be deleted.")
    limited = set()
    for body, pending in db.execute("SELECT body, pending FROM limits"):
        for raw in (body, pending):
            if not raw or raw == "null":
                continue
            for target in json.loads(raw).get("targets", []):
                if target.startswith("c:"):
                    limited.add(target[2:])
    blocked = sorted(missing & limited)
    if blocked:
        names_ = ", ".join(current[id_]["name"] for id_ in blocked)
        raise ValueError(f"Remove {names_} from your limits before deleting it.")
    with db:
        for i, row in enumerate(cleaned):
            id_ = row["id"]
            if not id_:
                while True:
                    id_ = "x" + secrets.token_hex(4)
                    if id_ not in current and id_ not in seen:
                        break
                seen.add(id_)
            old = current.get(id_)
            db.execute("INSERT INTO category_defs (id, name, color, about, position, custom) VALUES (?, ?, ?, ?, ?, ?) "
                       "ON CONFLICT(id) DO UPDATE SET name = excluded.name, color = excluded.color, "
                       "position = excluded.position",
                       (id_, row["name"], row["color"], old["about"] if old else "", i, int(old["custom"] if old else True)))
            row["id"] = id_
        for id_ in missing:
            db.execute("DELETE FROM category_defs WHERE id = ?", (id_,))
            db.execute("DELETE FROM categories WHERE cat = ?", (id_,))
        waiting = json.loads(kv_get(db, "category_pending") or "{}")
        for key, (cat, _) in list(waiting.items()):
            if cat in missing:
                del waiting[key]
        kv_set(db, "category_pending", json.dumps(waiting))
    return category_list(db)


# ---- days ----

def day_bounds(date):
    """[start, end) of a day in unix seconds. Local time, 04:00 to 04:00."""
    d = dt.date.fromisoformat(date) if isinstance(date, str) else date
    a = dt.datetime(d.year, d.month, d.day, DAY_START_HOUR)
    n = d + dt.timedelta(days=1)
    b = dt.datetime(n.year, n.month, n.day, DAY_START_HOUR)
    return a.timestamp(), b.timestamp()


def day_of(ts):
    """The day a moment belongs to, as YYYY-MM-DD."""
    return (dt.datetime.fromtimestamp(ts) - dt.timedelta(hours=DAY_START_HOUR)).date().isoformat()


def today(now=None):
    return day_of(now if now is not None else time.time())


def utc_offset(ts):
    return int(dt.datetime.fromtimestamp(ts).astimezone().utcoffset().total_seconds())


def shift(date, days):
    return (dt.date.fromisoformat(date) + dt.timedelta(days=days)).isoformat()


# ---- intervals ----

def merge(intervals, gap=0):
    """Union of [a, b) intervals, joining any that sit within `gap` of each other."""
    out = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if out and a <= out[-1][1] + gap:
            if b > out[-1][1]:
                out[-1][1] = b
        else:
            out.append([a, b])
    return out


def total(intervals):
    return sum(b - a for a, b in intervals)


def intersect(xs, ys):
    """Intersection of two merged, sorted interval lists."""
    out, i, j = [], 0, 0
    while i < len(xs) and j < len(ys):
        a, b = max(xs[i][0], ys[j][0]), min(xs[i][1], ys[j][1])
        if a < b:
            out.append([a, b])
        if xs[i][1] < ys[j][1]:
            i += 1
        else:
            j += 1
    return out


def clip(intervals, lo, hi):
    return [[max(a, lo), min(b, hi)] for a, b in intervals if b > lo and a < hi]


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


# ---- what a thing is ----

BROWSERS = {
    "zen", "zen-browser", "firefox", "librewolf", "floorp", "chromium", "google-chrome", "brave-browser",
    "vivaldi-stable", "microsoft-edge", "opera", "org.mozilla.firefox", "app.zen_browser.zen",
    "com.android.chrome", "org.mozilla.fenix", "org.mozilla.focus", "com.brave.browser", "com.opera.browser",
    "com.duckduckgo.mobile.android", "com.vivaldi.browser", "com.microsoft.emmx", "com.sec.android.app.sbrowser",
    "org.chromium.chrome", "com.kiwibrowser.browser", "io.github.zen_browser.zen", "org.mozilla.firefox_beta",
}
BROWSER_SUFFIXES = (
    "zen browser", "zen", "mozilla firefox", "firefox", "librewolf", "floorp", "chromium", "google chrome",
    "brave", "vivaldi", "microsoft edge", "opera", "mozilla firefox private browsing", "private browsing",
)
TITLE_SEPARATORS = (" — ", " – ", " - ", " | ", " · ")
# The desktop itself with no window focused. That's nobody using anything, so it is never recorded.
IGNORED_APPS = {"plasmashell", "org.kde.plasmashell", "", "kwin_wayland", "ksmserver", "kscreenlocker_greet"}


def site_from_caption(caption):
    """Best-effort site name from a browser window title, or "" when the title doesn't say.

    Titles look like "Video Name - YouTube — Zen Browser": split on the usual
    separators, drop the browser's own name, and look for the site. A piece
    that is a site we know wins; failing that the last piece, if it is short
    enough to be a name. A long last piece is the page's own title (a product
    listing, a document), which says nothing about where you were and is
    nobody's business, so it isn't kept: that time is just the browser's.
    It's read from the title, not the address, so it's a good guess and not a fact.
    """
    parts = [caption]
    for sep in TITLE_SEPARATORS:
        parts = [p for chunk in parts for p in chunk.split(sep)]
    parts = [p.strip() for p in parts if p.strip()]
    while parts and parts[-1].lower() in BROWSER_SUFFIXES:
        parts.pop()
    # "(3) YouTube" and "YouTube (3)" are the same place.
    parts = [re.sub(r"^\(\d+\+?\)\s*|\s*\(\d+\+?\)$", "", p).strip() for p in parts]
    parts = [p for p in parts if p]
    for p in reversed(parts):
        if p.lower() in KNOWN_SITES:
            return p
    return parts[-1] if parts and plausible_site(parts[-1]) else ""


# Phones read the site from the address bar, so they send a domain ("m.youtube.com") where computers send
# the name a window title gives ("YouTube"). This turns the one into the other, so both are one thing. The
# most specific domain listed wins: docs.google.com is Google Docs, the rest of google.com is Google.
SITE_NAMES = {
    "youtube.com": "YouTube", "youtu.be": "YouTube", "music.youtube.com": "YouTube Music",
    "reddit.com": "Reddit", "redd.it": "Reddit", "twitch.tv": "Twitch", "netflix.com": "Netflix",
    "primevideo.com": "Prime Video", "disneyplus.com": "Disney+", "crunchyroll.com": "Crunchyroll",
    "hulu.com": "Hulu", "max.com": "Max", "x.com": "X", "twitter.com": "X", "instagram.com": "Instagram",
    "tiktok.com": "TikTok", "facebook.com": "Facebook", "threads.net": "Threads", "threads.com": "Threads",
    "bsky.app": "Bluesky", "tumblr.com": "Tumblr", "pinterest.com": "Pinterest", "pinterest.ca": "Pinterest",
    "9gag.com": "9GAG", "imgur.com": "Imgur", "news.ycombinator.com": "Hacker News", "lobste.rs": "Lobsters",
    "wikipedia.org": "Wikipedia", "artificialanalysis.ai": "Artificial Analysis", "printables.com": "Printables",
    "makerworld.com": "MakerWorld", "thingiverse.com": "Thingiverse", "open.spotify.com": "Spotify",
    "github.com": "GitHub", "gitlab.com": "GitLab", "codeberg.org": "Codeberg", "stackoverflow.com": "Stack Overflow",
    "overleaf.com": "Overleaf", "figma.com": "Figma", "docs.google.com": "Google Docs",
    "sheets.google.com": "Google Sheets", "slides.google.com": "Google Slides", "colab.research.google.com": "Colab",
    "developer.mozilla.org": "MDN Web Docs", "wiki.archlinux.org": "Arch Wiki", "notion.so": "Notion",
    "replit.com": "Replit", "onshape.com": "Onshape", "claude.ai": "Claude", "chatgpt.com": "ChatGPT",
    "discord.com": "Discord", "web.whatsapp.com": "WhatsApp", "web.telegram.org": "Telegram",
    "mail.google.com": "Gmail", "mail.proton.me": "Proton Mail", "outlook.live.com": "Outlook",
    "outlook.office.com": "Outlook", "meet.google.com": "Google Meet", "messenger.com": "Messenger",
    "app.slack.com": "Slack", "monkeytype.com": "Monkeytype", "chess.com": "Chess.com", "lichess.org": "Lichess",
    "google.com": "Google", "google.ca": "Google", "duckduckgo.com": "DuckDuckGo", "bing.com": "Bing",
    "kagi.com": "Kagi", "maps.google.com": "Google Maps", "amazon.com": "Amazon", "amazon.ca": "Amazon",
    "ebay.com": "eBay", "ebay.ca": "eBay", "kijiji.ca": "Kijiji", "aliexpress.com": "AliExpress",
    "cbc.ca": "CBC", "bbc.com": "BBC", "bbc.co.uk": "BBC", "theverge.com": "The Verge",
    "arstechnica.com": "Ars Technica", "nytimes.com": "The New York Times", "theguardian.com": "The Guardian",
}
# Domains shared by many sites: the site is the label in front.
_SHARED_DOMAINS = {"github.io", "gitlab.io", "pages.dev", "vercel.app", "netlify.app", "herokuapp.com",
                   "blogspot.com", "substack.com", "wordpress.com", "fly.dev", "neocities.org"}
_HOST_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,63}$")


def site_from_host(host):
    """The name a domain goes by: "m.youtube.com" -> "YouTube", "foo.github.io" -> "foo.github.io".

    A domain nobody has named stays a domain, cut down to the part you'd type: "news.example.co.uk" ->
    "example.co.uk". "" if it isn't a domain at all.
    """
    h = host.strip().lower().rstrip(".")
    if not _HOST_RE.match(h):
        return ""
    labels = h.split(".")
    while len(labels) > 2 and labels[0] in ("www", "m", "mobile", "amp", "touch"):
        labels = labels[1:]
    for i in range(len(labels) - 1):
        name = SITE_NAMES.get(".".join(labels[i:]))
        if name:
            return name
    if labels[-2:] == ["ts", "net"] and len(labels) > 3:
        return labels[0]  # a tailnet: "lightcurve.tailnet-name.ts.net" is the service in front
    for i in range(1, len(labels) - 1):
        if ".".join(labels[i:]) in _SHARED_DOMAINS:
            return ".".join(labels[i - 1:])
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ("co", "com", "org", "net", "ac", "gov", "edu") else 2
    return ".".join(labels[-keep:])


def site_name(site):
    """A site as it is shown and known: a domain becomes its name, a name from a title stays as it is."""
    return (site_from_host(site) or site) if "." in site and " " not in site else site


def plausible_site(name):
    """Could this be the name of a site, as opposed to the title of a page?"""
    return bool(name) and len(name) <= 30 and len(name.split()) <= 4 and "@" not in name and name != "(untitled)"


_RULES = {
    "make": (
        "kitty konsole alacritty foot wezterm ghostty org.kde.konsole org.wezfurlong.wezterm com.termux "
        "code codium code-oss vscodium zed dev.zed.zed kate org.kde.kate kwrite neovide emacs gedit "
        "com.t3tools.t3code t3code t3 code claude com.anthropic.claude claude-desktop chatgpt com.openai.chatgpt "
        "obsidian md.obsidian.obsidian md.obsidian logseq notion "
        "orca-slicer orcaslicer bambu-studio prusa-slicer cura freecad org.freecad.freecad openscad kicad blender "
        "onshape com.onshape.app fusion "
        "onlyoffice libreoffice libreoffice-writer libreoffice-calc libreoffice-impress soffice "
        "gimp inkscape org.inkscape.inkscape krita org.kde.krita darktable rawtherapee kdenlive org.kde.kdenlive "
        "audacity ardour godot unity android-studio jetbrains-idea jetbrains-pycharm jetbrains-studio "
        "github gitlab stack overflow overleaf figma google docs google sheets google slides jupyter "
        "codeberg sourcehut replit colab google colab mdn web docs mdn arch wiki archwiki "
        "org.kde.okular okular com.github.xournalpp.xournalpp anki"
    ),
    "watch": (
        "youtube youtube music reddit twitch netflix prime video disney+ crunchyroll hulu max plex jellyfin "
        "vlc org.videolan.vlc mpv io.mpv.mpv haruna org.kde.haruna celluloid stremio cathode com.cathode.app exosphere "
        "x twitter instagram tiktok facebook threads bluesky mastodon tumblr pinterest 9gag imgur "
        "hacker news lobsters spotify com.spotify.music foliate com.github.johnfactotum.foliate "
        "com.google.android.youtube com.reddit.frontpage com.instagram.android com.zhiliaoapp.musically "
        "com.twitter.android com.facebook.katana com.netflix.mediaclient tv.twitch.android.app "
        "com.google.android.apps.youtube.music wikipedia artificial analysis printables makerworld thingiverse "
        "cbc bbc the verge ars technica the new york times the guardian"
    ),
    "talk": (
        "discord com.discordapp.discord vesktop dev.vencord.vesktop signal org.signal.signal org.thoughtcrime.securesms "
        "whatsapp com.whatsapp telegram org.telegram.desktop org.telegram.messenger element im.riot.riot "
        "thunderbird org.mozilla.thunderbird gmail com.google.android.gm proton mail ch.protonmail.android outlook "
        "slack com.slack microsoft teams com.microsoft.teams zoom us.zoom.videomeetings google meet messenger com.facebook.orca "
        "com.google.android.apps.messaging com.google.android.dialer org.kde.kdeconnect.sms kdeconnect-sms "
        "com.snapchat.android snapchat mail kmail org.kde.kmail2 geary com.google.android.contacts"
    ),
    "play": (
        "steam steam_proton gamescope lutris net.lutris.lutris heroic com.heroicgameslauncher.hgl "
        "minecraft prismlauncher org.prismlauncher.prismlauncher monkeytype chess.com lichess "
        "com.valvesoftware.android.steam.community retroarch dolphin-emu yuzu ryujinx"
    ),
    "other": (
        "systemsettings org.kde.dolphin dolphin org.kde.plasma-systemmonitor org.kde.spectacle spectacle "
        "org.kde.gwenview gwenview org.kde.filelight krunner kvantummanager lact kdiskmark org.kde.ark ark "
        "org.kde.discover org.kde.polkit-kde-authentication-agent-1 org.freedesktop.impl.portal.desktop.kde "
        "kcm_workspace com.android.settings com.android.systemui com.android.vending com.google.android.apps.nexuslauncher "
        "com.nothing.launcher com.teslacoilsw.launcher app.lawnchair com.android.launcher3 "
        "com.google.android.deskclock com.android.camera com.google.android.calculator org.kde.kcalc "
        "com.google.android.apps.photos com.google.android.permissioncontroller com.google.android.packageinstaller "
        "google google search duckduckgo bing kagi google maps com.google.android.apps.maps"
    ),
}
# Multi-word names are matched whole, so they're listed again here.
_PHRASES = {
    "make": ("stack overflow", "google docs", "google sheets", "google slides", "google colab", "mdn web docs",
             "arch wiki", "t3 code"),
    "watch": ("youtube music", "prime video", "hacker news", "artificial analysis", "the verge", "ars technica",
              "the new york times", "the guardian"),
    "talk": ("proton mail", "microsoft teams", "google meet"),
    "play": (),
    "other": ("google search", "google maps"),
}
BUILTIN = {}
for _cat, _words in _RULES.items():
    for _p in sorted((p for ps in _PHRASES.values() for p in ps), key=len, reverse=True):
        _words = re.sub(r"(?<!\S)" + re.escape(_p) + r"(?!\S)", " ", _words)  # a phrase isn't its words
    for _w in _words.split():
        BUILTIN.setdefault(_w, _cat)
for _cat, _phrases in _PHRASES.items():
    for _p in _phrases:
        BUILTIN[_p] = _cat
# Names that can be trusted as "the site" when they turn up as a whole piece of a window title.
KNOWN_SITES = {k for k in BUILTIN if k.count(".") <= 1 and "-" not in k and "_" not in k} \
    - {"code", "max", "mail", "foot", "ark", "fusion", "unity", "element", "signal", "steam"}
_GAME_RE = re.compile(r"^(steam_app_\d+|.*\.exe|gamescope.*)$")
HINTS = set(CATEGORIES)


class Namer:
    """Turns (app, site) into a key, a label and a category, using the user's own choices first."""

    def __init__(self, db):
        self.names = {a: (l, h) for a, l, h in db.execute("SELECT app, label, hint FROM names")}
        self.overrides = dict(db.execute("SELECT key, cat FROM categories"))
        self._cache = {}
        self._keys = {}

    def key(self, app, site):
        """Things are known by their name, so the YouTube app and the YouTube site are one thing."""
        k = (app, site)
        if k not in self._keys:
            self._keys[k] = "n:" + self.label(app, site).lower()
        return self._keys[k]

    def label(self, app, site):
        if site:
            return site_name(site)
        label = self.names.get(app, ("", ""))[0]
        return label or pretty_app(app)

    def category(self, app, site):
        k = (app, site)
        if k not in self._cache:
            self._cache[k] = self._category(app, site)
        return self._cache[k]

    def _category(self, app, site):
        key = self.key(app, site)
        if key in self.overrides:
            return self.overrides[key]
        label = self.label(app, site).lower()
        if not site and label in BUILTIN and app.lower() not in BUILTIN:
            return BUILTIN[label]
        if site:
            s = label
            if s in BUILTIN:
                return BUILTIN[s]
            # a domain nobody listed, named after something that is: "monkeytype.com"
            if "." in s and s.split(".")[0] in KNOWN_SITES:
                return BUILTIN[s.split(".")[0]]
            # "r/linux" style and "Something - YouTube" leftovers
            if s.startswith("r/"):
                return "watch"
            return "browse"
        a = app.lower()
        if a in BUILTIN:
            return BUILTIN[a]
        if _GAME_RE.match(a):
            return "play"
        if a in BROWSERS:
            return "browse"
        # what the phone says an app is comes last: Android's categories are loose
        label, hint = self.names.get(app, ("", ""))
        if label and label.lower() in BUILTIN:
            return BUILTIN[label.lower()]
        if hint in HINTS:
            return hint
        return "other"


_PRETTY = {
    "zen": "Zen", "kitty": "kitty", "com.t3tools.T3Code": "T3 Code", "gamescope": "Gamescope",
    "systemsettings": "System Settings", "steam_proton": "Proton", "claude-desktop": "Claude",
    "com.anthropic.Claude": "Claude", "md.obsidian.Obsidian": "Obsidian", "ONLYOFFICE": "OnlyOffice",
    "orca-slicer": "OrcaSlicer",
}


def pretty_app(app):
    """A readable name for a window class or package id when the device didn't send one."""
    if app in _PRETTY:
        return _PRETTY[app]
    m = re.match(r"^steam_app_(\d+)$", app)
    if m:
        return "Steam game " + m.group(1)
    name = app
    if re.match(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2,}$", app):  # reverse-DNS: keep the last part
        name = app.rsplit(".", 1)[-1]
    name = name.replace("-", " ").replace("_", " ").strip()
    return name[:1].upper() + name[1:] if name else app


# ---- reading spans ----

def load_spans(db, t0, t1):
    """Spans that touch [t0, t1), clipped to it, as (device, start, end, app, site) in seconds."""
    rows = db.execute(
        "SELECT device, start, end, app, site FROM spans WHERE end > ? AND start < ? ORDER BY device, start",
        (int(t0 * 1000), int(t1 * 1000)),
    ).fetchall()
    out = []
    for i, (device, start, end, app, site) in enumerate(rows):
        a, b = start / 1000, end / 1000
        # One device shows one thing at a time: a span never runs past the next one's start.
        if i + 1 < len(rows) and rows[i + 1][0] == device:
            b = min(b, rows[i + 1][1] / 1000)
        a, b = max(a, t0), min(b, t1)
        if b > a:
            out.append((device, a, b, app, site))
    return out


def load_pickups(db, t0, t1):
    return db.execute("SELECT device, ts FROM pickups WHERE ts >= ? AND ts < ? ORDER BY ts",
                      (int(t0 * 1000), int(t1 * 1000))).fetchall()


def devices(db):
    cols = ("id", "name", "kind", "platform", "version", "first_seen", "last_seen", "synced_to", "active_at_sync")
    return [dict(zip(cols, r)) for r in db.execute(
        "SELECT id, name, kind, platform, version, first_seen, last_seen, synced_to, active_at_sync "
        "FROM devices ORDER BY first_seen")]


def sessions_of(spans, namer):
    """Group spans into sessions: the same thing on the same device, with gaps under SESSION_GAP.

    Returns {key: [(device, start, end, seconds_on_it), ...]}.
    """
    out, open_ = {}, {}
    for device, a, b, app, site in spans:
        key = namer.key(app, site)
        cur = open_.get((device, key))
        if cur and a - cur[2] <= SESSION_GAP:
            cur[2] = b
            cur[3] += b - a
        else:
            cur = [device, a, b, b - a]
            open_[(device, key)] = cur
            out.setdefault(key, []).append(cur)
    return out


# ---- one day ----

class DayData:
    """Everything worked out for one day. Built once per day and cached by the hub."""

    def __init__(self, db, date, namer, now=None, device=None):
        """With `device`, the day as that one device saw it; the per-device totals still cover them all."""
        self.date = date
        self.device = device
        self.t0, self.t1 = day_bounds(date)
        self.now = now if now is not None else time.time()
        self.end = max(self.t0, min(self.t1, self.now))
        spans = load_spans(db, self.t0, self.end)
        self.namer = namer
        by_device = {}
        for d, a, b, _, _ in spans:
            by_device.setdefault(d, []).append((a, b))
        # waytime's totals stand in for a computer on the days it kept no spans of its own
        rows = [r for r in db.execute("SELECT device, app, seconds FROM legacy WHERE date = ?", (date,)).fetchall()
                if r[0] not in by_device]
        self.all_device_seconds = {d: total(merge(iv)) for d, iv in by_device.items()}
        for d in {r[0] for r in rows}:
            self.all_device_seconds[d] = legacy_things([r for r in rows if r[0] == d])[0]
        # every device together: screens on at once count once; waytime totals simply add
        self.all_seconds = total(merge([iv for ivs in by_device.values() for iv in ivs])) + sum(
            v for d, v in self.all_device_seconds.items() if d not in by_device)
        if device:
            spans = [sp for sp in spans if sp[0] == device]
            by_device = {d: iv for d, iv in by_device.items() if d == device}
            rows = [r for r in rows if r[0] == device]
        self.spans = spans
        self.pickups = [p for p in load_pickups(db, self.t0, self.end) if not device or p[0] == device]
        self.device_lit = {d: merge(iv) for d, iv in by_device.items()}
        self.lit = merge([iv for ivs in self.device_lit.values() for iv in ivs])
        self.lit_seconds = total(self.lit)
        self.summed_seconds = sum(total(iv) for iv in self.device_lit.values())
        self.sittings = merge(self.lit, SITTING_GAP)
        self.cat_seconds = dict.fromkeys(category_ids(db), 0.0)
        for _, a, b, app, site in self.spans:
            cat = namer.category(app, site)
            self.cat_seconds[cat] = self.cat_seconds.get(cat, 0.0) + b - a
        self.legacy = rows or None

    @property
    def has_data(self):
        return bool(self.spans)

    def lit_until(self, offset):
        """Seconds lit in the first `offset` seconds of the day."""
        return total(clip(self.lit, self.t0, self.t0 + offset))

    def switches(self):
        """Times one thing was swapped straight for another (not coming back from a break)."""
        n, last = 0, {}
        for device, a, b, app, site in self.spans:
            key = self.namer.key(app, site)
            prev = last.get(device)
            if prev and prev[0] != key and a - prev[1] <= SESSION_GAP:
                n += 1
            last[device] = (key, b)
        return n

    def metrics(self):
        """The handful of numbers a day is compared on."""
        m = {
            "lit": self.lit_seconds,
            "first": self.lit[0][0] - self.t0 if self.lit else None,
            "last": self.lit[-1][1] - self.t0 if self.lit else None,
            "switches": self.switches(),
            "longest_sitting": max((b - a for a, b in self.sittings), default=0),
            "sittings": len(self.sittings),
            "pickups": len(self.pickups),
        }
        hours = self.summed_seconds / 3600
        m["switch_rate"] = m["switches"] / hours if hours > 0.25 else None
        for c, seconds in self.cat_seconds.items():
            m[c] = seconds
        return m


def _fmt_entity(namer, key, app, site):
    return {"key": key, "label": namer.label(app, site), "cat": namer.category(app, site)}


def _part(app, site):
    """One concrete form of a thing: a site by its name, or an app by its id."""
    return ("site", site) if site else ("app", app)


def _fmt_parts(parts, label, cat, blocked):
    out = []
    for kind, ident in sorted(parts):
        p = {"kind": kind, "id": ident, "label": label, "cat": cat}
        p["blocked"] = bool(blocked(p))
        out.append(p)
    return out


def day_report(db, date, cache=None, now=None, burns=(), blocked=None, device=None):
    """The full account of one day, as plain data for the dashboard and the CLI."""
    now = now if now is not None else time.time()
    namer = Namer(db)
    day = DayData(db, date, namer, now, device)
    t0, t1 = day.t0, day.t1
    devs = devices(db)
    dev_by_id = {d["id"]: d for d in devs}
    nbins = max(1, round((t1 - t0) / BIN))

    # light curve: how much of each ten minutes was lit, and by what kind of thing
    bin_cat = [dict() for _ in range(nbins)]
    bin_top = [dict() for _ in range(nbins)]
    ent, ent_devices, ent_parts = {}, {}, {}
    for device, a, b, app, site in day.spans:
        key = namer.key(app, site)
        cat = namer.category(app, site)
        e = ent.get(key)
        if e is None:
            e = ent[key] = {**_fmt_entity(namer, key, app, site), "seconds": 0.0}
        e["seconds"] += b - a
        ent_devices.setdefault(key, set()).add(device)
        ent_parts.setdefault(key, set()).add(_part(app, site))
        i = int((a - t0) // BIN)
        while i < nbins and t0 + i * BIN < b:
            lo, hi = max(a, t0 + i * BIN), min(b, t0 + (i + 1) * BIN)
            if hi > lo:
                bin_cat[i][cat] = bin_cat[i].get(cat, 0) + hi - lo
                bin_top[i][key] = bin_top[i].get(key, 0) + hi - lo
            i += 1
    lit_bins = [0.0] * nbins
    for a, b in day.lit:
        i = int((a - t0) // BIN)
        while i < nbins and t0 + i * BIN < b:
            lit_bins[i] += max(0, min(b, t0 + (i + 1) * BIN) - max(a, t0 + i * BIN))
            i += 1
    curve = []
    for i in range(nbins):
        if lit_bins[i] <= 0:
            curve.append(None)
            continue
        cats = bin_cat[i]
        top = sorted(bin_top[i].items(), key=lambda kv: -kv[1])[:3]
        curve.append({
            "lit": round(lit_bins[i] / BIN, 3),
            "cat": max(cats, key=cats.get),
            "cats": {c: round(s) for c, s in cats.items()},
            "top": [[ent[k]["label"], round(s)] for k, s in top],
        })

    # per-device strips: what was on each screen, when
    strips = []
    for d in devs:
        rows = []
        for device, a, b, app, site in day.spans:
            if device != d["id"]:
                continue
            key = namer.key(app, site)
            if rows and rows[-1][2] == key and a - rows[-1][1] <= 5:
                rows[-1][1] = b
            else:
                rows.append([a, b, key])
        rows = [[round(a - t0, 1), round(b - t0, 1), key] for a, b, key in rows if b - a >= 2]
        picks = [round(ts / 1000 - t0) for dev, ts in day.pickups if dev == d["id"]]
        if rows or picks:
            strips.append({"device": d["id"], "seconds": round(total(day.device_lit.get(d["id"], []))), "spans": rows,
                           "pickups": picks})

    sess = sessions_of(day.spans, namer)
    blocked = blocked or (lambda p: False)
    entities = []
    for key, e in ent.items():
        ss = sess.get(key, [])
        parts = _fmt_parts(ent_parts[key], e["label"], e["cat"], blocked)
        entities.append({
            **e, "seconds": round(e["seconds"]), "sessions": len(ss),
            "devices": sorted(ent_devices[key]), "parts": parts,
            "blocked": all(p["blocked"] for p in parts),
        })
    entities.sort(key=lambda e: -e["seconds"])

    # the longest unbroken stretch on one thing
    stretch = None
    for key, ss in sess.items():
        for device, a, b, on in ss:
            if stretch is None or on > stretch["seconds"]:
                stretch = {"seconds": round(on), "label": ent[key]["label"], "cat": ent[key]["cat"],
                           "start": round(a - t0), "end": round(b - t0), "device": device}

    # pickups, and how many came while a computer was already in use
    desk = merge([iv for d, ivs in day.device_lit.items()
                  if dev_by_id.get(d, {}).get("kind") != "phone" for iv in ivs], 60)
    while_desk = 0
    for _, ts in day.pickups:
        s = ts / 1000
        if any(a - 30 <= s <= b + 30 for a, b in desk):
            while_desk += 1
    phones = [d for d, ivs in day.device_lit.items() if dev_by_id.get(d, {}).get("kind") == "phone"]
    both = intersect(merge([iv for d in phones for iv in day.device_lit[d]]), merge(desk))

    longest = max(day.sittings, key=lambda iv: iv[1] - iv[0], default=None)
    metrics = day.metrics()

    report = {
        "date": date, "today": today(now), "t0": t0, "t1": t1, "now": now, "utc_offset": utc_offset(t0 + 43200),
        "in_progress": t0 <= now < t1, "has_data": day.has_data,
        "lit": round(day.lit_seconds), "summed": round(day.summed_seconds),
        "both": round(total(both)),
        "first": {"at": round(day.lit[0][0] - t0)} if day.lit else None,
        "last": {"at": round(day.lit[-1][1] - t0)} if day.lit else None,
        "curve": curve, "strips": strips, "entities": entities,
        "categories": {c: round(s) for c, s in day.cat_seconds.items()},
        "category_defs": category_list(db),
        "devices": [{"id": d["id"], "name": d["name"], "kind": d["kind"],
                     "seconds": round(day.all_device_seconds.get(d["id"], 0))} for d in devs],
        "device": device, "all_seconds": round(day.all_seconds),
        "attention": {
            "switches": metrics["switches"], "switch_rate": metrics["switch_rate"],
            "sittings": len(day.sittings),
            "longest_sitting": {"seconds": round(longest[1] - longest[0]), "start": round(longest[0] - t0),
                                "end": round(longest[1] - t0)} if longest else None,
            "stretch": stretch,
            "pickups": len(day.pickups), "pickups_while_desk": while_desk,
            "pickup_times": [round(ts / 1000 - t0) for _, ts in day.pickups],
        },
        "legacy": None,
    }
    if day.lit:
        first = min(day.spans, key=lambda s: s[1])
        last = max(day.spans, key=lambda s: s[2])
        report["first"].update(device=first[0], label=namer.label(first[3], first[4]))
        report["last"].update(device=last[0], label=namer.label(last[3], last[4]))
    if day.legacy:
        seconds, things = legacy_things(day.legacy)
        merged = {}
        for (a, site), sec in things.items():
            label = namer.label(a, site)
            row = merged.setdefault(label.lower(), [label, 0, namer.category(a, site)])
            row[1] += sec
        report["legacy"] = {
            "seconds": round(seconds),
            "apps": sorted(([label, round(sec), cat] for label, sec, cat in merged.values()), key=lambda r: -r[1]),
        }

    # the day's burns, and what the screens were doing during each
    report["burns"] = []
    for burn in burns:
        a, b = max(burn["start"], t0), min(burn["end"], day.end if burn.get("live") else t1)
        if b <= a or burn["end"] <= t0 or burn["start"] >= t1:
            continue
        report["burns"].append({**burn_detail(day.spans, namer, burn["start"], min(burn["end"], now)),
                                "id": burn.get("id"), "start": round(burn["start"] - t0),
                                "end": round(burn["end"] - t0), "minutes": burn.get("minutes"),
                                "early": bool(burn.get("early")), "live": bool(burn.get("live"))})

    report["usual"] = usual(db, date, day, cache, now, device)
    report["notes"] = coverage_notes([d for d in devs if not device or d["id"] == device], day, now)
    return report


def burn_detail(spans, namer, a, b):
    """What was on screen during [a, b): seconds by category and the biggest non-work things."""
    cats, leaks, lit = {}, {}, []
    for device, s, e, app, site in spans:
        lo, hi = max(s, a), min(e, b)
        if hi <= lo:
            continue
        cat = namer.category(app, site)
        cats[cat] = cats.get(cat, 0.0) + hi - lo
        lit.append((lo, hi))
        if cat in ("watch", "play", "browse"):
            label = namer.label(app, site)
            leaks[label] = leaks.get(label, 0) + hi - lo
    return {
        "cats": {c: round(s) for c, s in cats.items()},
        "lit": round(total(merge(lit))),
        "leaks": sorted(([l, round(s)] for l, s in leaks.items() if s >= 20), key=lambda r: -r[1])[:4],
    }


class Cache:
    """Finished days don't change, so their DayData is kept. Ingest drops the days it touches."""

    def __init__(self):
        self.days = {}
        self.lock = threading.Lock()

    def get(self, db, date, now, device=None):
        t0, t1 = day_bounds(date)
        if now < t1 + 3600:  # still filling in, or late syncs still likely
            return DayData(db, date, Namer(db), now, device)
        with self.lock:
            day = self.days.get((date, device))
        if day is None:
            day = DayData(db, date, Namer(db), now, device)
            with self.lock:
                self.days[(date, device)] = day
        return day

    def drop(self, dates=None):
        with self.lock:
            if dates is None:
                self.days.clear()
            else:
                dates = set(dates)
                for k in [k for k in self.days if k[0] in dates]:
                    del self.days[k]


def usual(db, date, day, cache, now, device=None):
    """What these numbers usually are: the median of the 28 days before, for days that have data."""
    cache = cache or Cache()
    prior = []
    for i in range(1, BASELINE_DAYS + 1):
        d = cache.get(db, shift(date, -i), now, device)
        if d.has_data:
            prior.append(d)
    out = {"days": len(prior), "enough": len(prior) >= MIN_BASELINE}
    if not out["enough"]:
        return out
    ms = [d.metrics() for d in prior]
    cat_keys = set(day.cat_seconds)
    for d in prior:
        cat_keys.update(d.cat_seconds)
    for k in ("lit", "first", "last", "switches", "switch_rate", "longest_sitting", "pickups", "sittings",
              *sorted(cat_keys)):
        v = median([m.get(k, 0 if k in cat_keys else None) for m in ms])
        out[k] = round(v, 2) if v is not None else None
    if day.t0 <= now < day.t1:
        offset = now - day.t0
        out["lit_by_now"] = round(median([d.lit_until(offset) for d in prior]))
    # the last two weeks of each, oldest first, for the small trend lines
    recent = []
    for i in range(14, 0, -1):
        d = cache.get(db, shift(date, -i), now, device)
        recent.append(d.metrics() if d.has_data else None)
    out["recent"] = {k: [round(m[k], 2) if m and m[k] is not None else None for m in recent]
                     for k in ("lit", "switch_rate", "longest_sitting", "pickups", "first", "last")}
    return out


def coverage_notes(devs, day, now):
    """Say plainly where the record might be incomplete."""
    notes = []
    for d in devs:
        synced = d["synced_to"] / 1000
        if synced <= 0 or synced >= min(day.t1, now) - 2700:
            continue
        if synced >= day.t1:
            continue
        # A phone records whether or not it can reach the hub, so anything after its last sync is unknown.
        # A computer that went quiet is normally just off, unless it was mid-use when it stopped reporting.
        if d["kind"] == "phone" or d["active_at_sync"]:
            if now - synced > 2700 and synced < day.t1:
                notes.append({"device": d["id"], "name": d["name"], "since": round(max(synced, day.t0) - day.t0),
                              "kind": d["kind"], "silent_for": round(now - synced)})
    return notes


_FILE_RE = re.compile(r"\.(docx?|pdf|xlsx?|pptx?|od[tsp]|txt|md|png|jpe?g|stl|3mf|step)$")
_DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,6}$")


def _legacy_site(name):
    """waytime kept the last piece of any browser title as a "site", documents and all. Keep the real sites."""
    n = name.lower()
    if n in KNOWN_SITES:
        return True
    if " " in name or _FILE_RE.search(n):
        return False
    return name.isalpha() or bool(_DOMAIN_RE.match(n))


def legacy_things(rows):
    """Daily totals carried over from waytime: (seconds, {(app, site): seconds}).

    waytime kept a total per app and, separately, a total per site inside the
    browsers. Site rows arrive as "site:<name>"; they are a breakdown of browser
    time, so they come out of the browsers' share and never add to the total.
    """
    apps, sites = {}, {}
    for _, app, seconds in rows:
        if app.startswith("site:"):
            sites[app[5:]] = sites.get(app[5:], 0) + seconds
        elif app not in IGNORED_APPS:
            apps[app] = apps.get(app, 0) + seconds
    things = {(a, ""): s for a, s in apps.items() if a not in BROWSERS}
    browser = sum(s for a, s in apps.items() if a in BROWSERS)
    kept = 0
    for site, s in sites.items():
        if _legacy_site(site):
            things[("browser", site)] = s
            kept += s
    rest = browser - kept
    if rest > 60:
        main = max((a for a in apps if a in BROWSERS), key=lambda a: apps[a])
        things[(main, "")] = rest
    return sum(apps.values()), things


# ---- many days ----

def range_report(db, end_date, days, cache=None, now=None, burns=(), blocked=None, device=None):
    """Patterns over a run of days ending on `end_date`."""
    now = now if now is not None else time.time()
    cache = cache or Cache()
    namer = Namer(db)
    dates = [shift(end_date, -i) for i in range(days - 1, -1, -1)]
    t_start, t_end = day_bounds(dates[0])[0], min(day_bounds(dates[-1])[1], now)
    devs = devices(db)
    dev_by_id = {d["id"]: d for d in devs}
    blocked = blocked or (lambda p: False)

    rows, raster = [], []
    week = [[0.0] * 24 for _ in range(7)]
    week_days = [0] * 7
    all_spans = []
    legacy_apps = {}
    for date in dates:
        day = cache.get(db, date, now, device)
        t0, t1 = day.t0, day.t1
        nb = max(1, round((t1 - t0) / BIN))
        row = {"date": date, "lit": round(day.lit_seconds), "has_data": day.has_data,
               "weekday": dt.date.fromisoformat(date).weekday(),
               "first": round(day.lit[0][0] - t0) if day.lit else None,
               "last": round(day.lit[-1][1] - t0) if day.lit else None,
               "pickups": len(day.pickups), "cats": {c: round(s) for c, s in day.cat_seconds.items()},
               "devices": {d: round(v) for d, v in day.all_device_seconds.items()},
               "all_seconds": round(day.all_seconds),
               "burn_seconds": 0, "legacy": False}
        if day.legacy:
            seconds, things = legacy_things(day.legacy)
            row["legacy"] = True
            row["legacy_seconds"] = round(seconds)
            row["lit"] += round(seconds)
            for (app, site), sec in things.items():
                label = namer.label(app, site)
                item = legacy_apps.setdefault(label.lower(), [label, 0, namer.category(app, site)])
                item[1] += sec
        lit = [0.0] * nb
        cat = [dict() for _ in range(nb)]
        for _, a, b, app, site in day.spans:
            c = namer.category(app, site)
            i = int((a - t0) // BIN)
            while i < nb and t0 + i * BIN < b:
                s = max(0, min(b, t0 + (i + 1) * BIN) - max(a, t0 + i * BIN))
                cat[i][c] = cat[i].get(c, 0) + s
                i += 1
        for a, b in day.lit:
            i = int((a - t0) // BIN)
            while i < nb and t0 + i * BIN < b:
                lit[i] += max(0, min(b, t0 + (i + 1) * BIN) - max(a, t0 + i * BIN))
                i += 1
        raster.append({
            "lit": "".join("0123456789"[min(9, int(v / BIN * 9.999))] if v > 0 else "." for v in lit),
            "cat": "".join(max(c, key=c.get)[0] if c else "." for c in cat),
            "cat_ids": [max(c, key=c.get) if c else None for c in cat],
        })
        if day.has_data:
            wd = row["weekday"]
            week_days[wd] += 1
            for a, b in day.lit:
                h = int((a - t0) // 3600)
                while h < 24 and t0 + h * 3600 < b:
                    week[wd][h] += max(0, min(b, t0 + (h + 1) * 3600) - max(a, t0 + h * 3600))
                    h += 1
        rows.append(row)
        all_spans.extend(day.spans)

    # the census: every app and site, by how long a visit lasts and how much it adds up to
    sess = sessions_of(all_spans, namer)
    first_seen, parts_of = {}, {}
    for device, a, b, app, site in all_spans:
        key = namer.key(app, site)
        first_seen.setdefault(key, (app, site))
        parts_of.setdefault(key, set()).add(_part(app, site))
    census = []
    for key, ss in sess.items():
        app, site = first_seen[key]
        seconds = sum(s[3] for s in ss)
        if seconds < 60:
            continue
        lengths = sorted(s[3] for s in ss)
        e = _fmt_entity(namer, key, app, site)
        parts = _fmt_parts(parts_of[key], e["label"], e["cat"], blocked)
        census.append({
            **e, "seconds": round(seconds), "sessions": len(ss),
            "median": round(statistics.median(lengths), 1),
            "longest": round(lengths[-1]),
            "days": len({day_of(s[1]) for s in ss}),
            "devices": sorted({s[0] for s in ss}),
            "parts": parts, "blocked": all(p["blocked"] for p in parts),
        })
    census.sort(key=lambda e: -e["seconds"])

    data_days = [r for r in rows if r["has_data"]]
    rhythm = [[round(week[wd][h] / week_days[wd] / 60, 1) if week_days[wd] else None for h in range(24)]
              for wd in range(7)]

    cat_keys = set().union(*(r["cats"] for r in rows)) if rows else set()
    out = {
        "end": end_date, "days": days, "dates": dates, "t0": t_start, "utc_offset": utc_offset(t_start + 43200),
        "rows": rows, "raster": raster, "census": census[:150], "rhythm": rhythm,
        "rhythm_days": week_days,
        "data_days": len(data_days),
        "lit": sum(r["lit"] for r in rows),
        "average": round(sum(r["lit"] for r in data_days) / len(data_days)) if data_days else 0,
        "categories": {c: sum(r["cats"].get(c, 0) for r in rows) for c in cat_keys},
        "category_defs": category_list(db),
        "devices": [{"id": d["id"], "name": d["name"], "kind": d["kind"],
                     "seconds": sum(r["devices"].get(d["id"], 0) for r in rows)} for d in devs],
        "pickups": sum(r["pickups"] for r in rows),
        "device": device, "all_seconds": sum(r["all_seconds"] for r in rows),
        # waytime totals in these days, by name: [label, seconds, category]
        "legacy_apps": sorted(([l, round(sec), c] for l, sec, c in legacy_apps.values()), key=lambda r: -r[1]),
    }
    out["ignition"] = ignition_report(all_spans, namer, rows, burns, t_start, t_end, now)
    out["candidates"] = [e for e in census if e["cat"] in ("watch", "play", "browse") and not e["blocked"]
                         and e["label"] != "(untitled)"][:8]
    return out


def ignition_report(spans, namer, rows, burns, t_start, t_end, now):
    """What burns did to the screens: during them, right after them, and on the days that had one."""
    burns = [b for b in burns if b["end"] > t_start and b["start"] < t_end]
    if not burns:
        return {"burns": 0}
    during, after, overall = {}, {}, {}
    leaks, burn_seconds, lit_during = {}, 0.0, []
    windows = [(b["start"], min(b["end"], now)) for b in burns]
    rebound = [(b["end"], b["end"] + 1800) for b in burns if not b.get("live") and b["end"] + 1800 <= now]
    for device, s, e, app, site in spans:
        cat = namer.category(app, site)
        overall[cat] = overall.get(cat, 0.0) + e - s
        for a, b in windows:
            lo, hi = max(s, a), min(e, b)
            if hi > lo:
                during[cat] = during.get(cat, 0.0) + hi - lo
                lit_during.append((lo, hi))
                if cat in ("watch", "play", "browse"):
                    label = namer.label(app, site)
                    leaks[label] = leaks.get(label, 0) + hi - lo
        for a, b in rebound:
            lo, hi = max(s, a), min(e, b)
            if hi > lo:
                after[cat] = after.get(cat, 0.0) + hi - lo
    by_date = {r["date"]: r for r in rows}
    for b in burns:
        burn_seconds += min(b["end"], now) - b["start"]
        r = by_date.get(day_of(b["start"]))
        if r is not None:
            r["burn_seconds"] += round(min(b["end"], now) - b["start"])
    with_burn = [r for r in rows if r["has_data"] and r["burn_seconds"] > 0]
    without = [r for r in rows if r["has_data"] and r["burn_seconds"] == 0]

    def side(rs):
        return {
            "days": len(rs),
            "lit": round(median([r["lit"] for r in rs]) or 0),
            "drift": round(median([r["cats"]["watch"] + r["cats"]["play"] for r in rs]) or 0),
            "make": round(median([r["cats"]["make"] for r in rs]) or 0),
        }
    return {
        "burns": len(burns), "seconds": round(burn_seconds),
        "lit_during": round(total(merge(lit_during))),
        "during": {c: round(s) for c, s in during.items()},
        "after": {c: round(s) for c, s in after.items()},
        "after_windows": len(rebound),
        "overall": {c: round(s) for c, s in overall.items()},
        "leaks": sorted(([l, round(s)] for l, s in leaks.items() if s >= 30), key=lambda r: -r[1])[:6],
        "with": side(with_burn), "without": side(without),
        "enough": len(with_burn) >= 3 and len(without) >= 3,
        # each burn as seconds into its own day, for drawing over the record
        "list": [{"date": day_of(b["start"]), "start": round(b["start"] - day_bounds(day_of(b["start"]))[0]),
                  "end": round(min(b["end"], now) - day_bounds(day_of(b["start"]))[0])} for b in burns],
    }


# ---- taking data in ----

def _text(v, n=200):
    return str(v if v is not None else "")[:n]


def ingest(db, body, now=None):
    """Store what a device sends. Safe to repeat: spans are keyed by (device, start).

    Returns (result, touched_dates).
    """
    now = now if now is not None else time.time()
    dev = body.get("device") or {}
    dev_id = _text(dev.get("id"), 64).strip()
    if not re.match(r"^[A-Za-z0-9._:-]{3,64}$", dev_id):
        raise ValueError("device.id is missing or malformed")
    name = _text(dev.get("name") or dev_id, 60).strip() or dev_id
    kind = dev.get("kind") if dev.get("kind") in ("desktop", "laptop", "phone", "tablet") else "desktop"
    ms_now = int(now * 1000)
    limit = ms_now + 300_000  # nothing from the future, give or take a wrong clock
    touched = set()

    # Phone apps before 1.0.2 counted an app that opened behind a dark screen until the screen next came on,
    # which could be all night. Their long spans can't be trusted.
    try:
        old_phone = kind == "phone" and tuple(int(x) for x in str(dev.get("version") or "").split(".")[:3]) < (1, 0, 2)
    except ValueError:
        old_phone = False
    spans = []
    for row in (body.get("spans") or [])[:50000]:
        try:
            start, end, app = int(row[0]), int(row[1]), _text(row[2]).strip()
            site = _text(row[3] if len(row) > 3 else "", 80).strip()
        except (TypeError, ValueError, IndexError):
            continue
        if not app or end <= start or start < 946684800000 or end > limit or end - start > 86_400_000:
            continue
        if old_phone and end - start > 7_200_000:
            continue
        spans.append((dev_id, start, end, app, site))
        touched.add(day_of(start / 1000))
        touched.add(day_of(end / 1000))
    pickups = []
    for ts in (body.get("pickups") or [])[:20000]:
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            continue
        if 946684800000 < ts <= limit:
            pickups.append((dev_id, ts))
            touched.add(day_of(ts / 1000))

    with db:
        db.execute(
            "INSERT INTO devices (id, name, kind, platform, version, first_seen, last_seen) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, platform = excluded.platform, "
            "version = excluded.version, last_seen = excluded.last_seen, "
            "name = CASE WHEN renamed THEN name ELSE excluded.name END",
            (dev_id, name, kind, _text(dev.get("platform"), 40), _text(dev.get("version"), 20), ms_now, ms_now))
        if body.get("complete", True):
            # The device vouches that it has now sent everything it knows up to this moment.
            db.execute("UPDATE devices SET synced_to = ?, active_at_sync = ? WHERE id = ?",
                       (ms_now, 1 if body.get("active") else 0, dev_id))
        try:
            replace_from = int(body.get("replace_from") or 0)
        except (TypeError, ValueError):
            replace_from = 0
        if replace_from > 946684800000:
            # The device has rebuilt its record from here on under better rules: its old spans go.
            db.execute("DELETE FROM spans WHERE device = ? AND start >= ?", (dev_id, replace_from))
            day = day_of(replace_from / 1000)
            while day <= day_of(now):
                touched.add(day)
                day = shift(day, 1)
        db.executemany(
            "INSERT INTO spans (device, start, end, app, site) VALUES (?,?,?,?,?) "
            "ON CONFLICT(device, start) DO UPDATE SET end = excluded.end, app = excluded.app, site = excluded.site",
            spans)
        db.executemany("INSERT OR IGNORE INTO pickups (device, ts) VALUES (?,?)", pickups)
        for app, info in list((body.get("names") or {}).items())[:5000]:
            info = info if isinstance(info, dict) else {"label": info}
            hint = info.get("hint") if info.get("hint") in HINTS else ""
            db.execute(
                "INSERT INTO names (app, label, hint) VALUES (?,?,?) ON CONFLICT(app) DO UPDATE SET "
                "label = CASE WHEN excluded.label != '' THEN excluded.label ELSE label END, "
                "hint = CASE WHEN excluded.hint != '' THEN excluded.hint ELSE hint END",
                (_text(app), _text(info.get("label"), 80).strip(), hint))
        legacy = 0
        for row in (body.get("legacy") or [])[:200000]:
            try:
                date, app, seconds = dt.date.fromisoformat(str(row[0])).isoformat(), _text(row[1]), float(row[2])
            except (TypeError, ValueError, IndexError):
                continue
            if app and seconds > 0:
                db.execute("INSERT OR REPLACE INTO legacy (device, date, app, seconds) VALUES (?,?,?,?)",
                           (dev_id, date, app, seconds))
                legacy += 1
                touched.add(date)
    return {"ok": True, "spans": len(spans), "pickups": len(pickups), "legacy": legacy, "now": ms_now}, touched


def fmt_duration(seconds):
    seconds = int(round(seconds))
    h, m = divmod(seconds // 60, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m"
    return f"{seconds}s"
