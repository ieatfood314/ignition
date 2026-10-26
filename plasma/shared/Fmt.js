.pragma library
// Text for the widgets, in the Android widgets' shouty dot-matrix style: 25M, 1H30M, TOMORROW, 3D LATE.

const DAY = 86400000;

// 28:14, or 1:05:00 past an hour.
function clock(sec) {
    sec = Math.max(0, Math.ceil(sec));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(s).padStart(2, "0");
    return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function estimate(min) {
    if (min == null)
        return "";
    if (min < 60)
        return `${min}M`;
    return min % 60 ? `${Math.floor(min / 60)}H${min % 60}M` : `${min / 60}H`;
}

function duration(sec) {
    return estimate(Math.floor(sec / 60));
}

// 6:35 PM, in the desktop's own time format.
function time(ts) {
    return Qt.formatTime(new Date(ts * 1000), Qt.locale().timeFormat(1 /* Locale.ShortFormat */)).toUpperCase();
}

function startOfToday() {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
}

// "TUE 9:00 AM", or just the time if it's today.
function when(ts) {
    const d = new Date(ts * 1000);
    const days = Math.round((new Date(d).setHours(0, 0, 0, 0) - startOfToday()) / DAY);
    const day = days === 0 ? "" : days === 1 ? "TOMORROW " : Qt.formatDate(d, "ddd").toUpperCase() + " ";
    return day + time(ts);
}

function parseDate(s) {
    const [y, m, d] = String(s).split("-").map(Number);
    return new Date(y, m - 1, d);
}

// { text: "TOMORROW", hot: false }; hot means due today or late.
function due(s) {
    const d = parseDate(s);
    if (isNaN(d))
        return { text: String(s), hot: false };
    const diff = Math.round((d - startOfToday()) / DAY);
    let text;
    if (diff < -1)
        text = `${-diff}D LATE`;
    else if (diff === -1)
        text = "YESTERDAY";
    else if (diff === 0)
        text = "TODAY";
    else if (diff === 1)
        text = "TOMORROW";
    else if (diff < 7)
        text = Qt.formatDate(d, "ddd").toUpperCase();
    else
        text = Qt.formatDate(d, "MMM d").toUpperCase();
    return { text, hot: diff <= 0 };
}

// The Need list's "smart" order, as on the website: due date, then priority, then oldest.
function upNext(items) {
    const nullsLast = (a, b, cmp) => (a == null) - (b == null) || (a != null && b != null ? cmp(a, b) : 0);
    return items.filter((i) => i.list === "need" && !i.done_at && !i.deleted).sort((a, b) =>
        nullsLast(a.due, b.due, (x, y) => x.localeCompare(y)) || b.priority - a.priority || a.created_at - b.created_at);
}
