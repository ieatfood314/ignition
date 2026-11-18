"""ignition's side of the record: its block list, as the screen-time pages show and change it.

These came from lightcurve's link to ignition, from back when the two were separate apps.
"""

import re

# Site names come from window titles, the block list wants addresses. These are the ones worth knowing
# by heart; anything else gets a guess the user can correct before it's sent.
DOMAINS = {
    "youtube": "youtube.com", "reddit": "reddit.com", "twitch": "twitch.tv", "netflix": "netflix.com",
    "x": "x.com", "twitter": "x.com", "instagram": "instagram.com", "tiktok": "tiktok.com",
    "facebook": "facebook.com", "hacker news": "news.ycombinator.com", "wikipedia": "wikipedia.org",
    "discord": "discord.com", "prime video": "primevideo.com", "disney+": "disneyplus.com",
    "bluesky": "bsky.app", "tumblr": "tumblr.com", "pinterest": "pinterest.com", "imgur": "imgur.com",
    "monkeytype": "monkeytype.com", "chess.com": "chess.com", "lichess": "lichess.org",
    "artificial analysis": "artificialanalysis.ai", "printables": "printables.com",
    "makerworld": "makerworld.com", "thingiverse": "thingiverse.com", "youtube music": "music.youtube.com",
    "crunchyroll": "crunchyroll.com", "threads": "threads.net", "9gag": "9gag.com",
}


def slug(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def guess_domain(site):
    s = site.lower().strip()
    if s in DOMAINS:
        return DOMAINS[s]
    if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$", s):
        return s
    return (slug(s) or "example") + ".com"


def blocked_test(block):
    """A function saying whether an app or site is already on the burn block list."""
    sites = {slug(s.split(".")[0] if s.count(".") == 1 else s.rsplit(".", 2)[-2]) for s in block.get("sites", [])}
    full = {s.lower() for s in block.get("sites", [])}
    apps = {a["id"] if isinstance(a, dict) else a for a in block.get("apps", [])}
    programs = {p.lower() for p in block.get("programs", [])}
    games = bool(block.get("games"))

    def test(p):
        if p["kind"] == "site":
            name = p["id"].lower()
            return name in full or guess_domain(name) in full or slug(name) in sites
        app = p["id"]
        if app in apps or app.lower() in programs:
            return True
        return games and p.get("cat") == "play"
    return test


def suggest(p):
    """How one form of a thing would go onto the block list: (list, value)."""
    if p["kind"] == "site":
        return "sites", guess_domain(p["id"])
    # Android package ids go on the phone's app list; anything else is a program on a computer.
    if re.match(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$", p["id"]) and not p["id"].startswith(("org.kde.", "org.freedesktop.")):
        return "apps", p["id"]
    return "programs", p["id"].lower()
