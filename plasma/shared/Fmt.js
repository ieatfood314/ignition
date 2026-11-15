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
