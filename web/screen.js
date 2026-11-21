"use strict";
/* Screen time and limits (what lightcurve was): the Screen and Limits tabs, the Devices settings page and
   the screen-time line on Ignite. Runs after app.js and uses its helpers ($, api, esc, toast, state, ...).
   Days run 04:00 to 04:00, as the server counts them. */

/* ---- shared ---- */

const DEFAULT_CATS = [
  { id: "make", name: "Make", color: "#ffffff", about: "Writing, building, coding", custom: false },
  { id: "talk", name: "Talk", color: "#a3a3a3", about: "Messages, mail, calls", custom: false },
  { id: "other", name: "Other", color: "#575757", about: "Settings, files, home screen", custom: false },
  { id: "browse", name: "Browse", color: "#6e2219", about: "Other web pages", custom: false },
  { id: "watch", name: "Watch", color: "#a92e1f", about: "Video, feeds, reading", custom: false },
  { id: "play", name: "Play", color: "#e23a29", about: "Games", custom: false },
];
let CAT_DEFS = DEFAULT_CATS, CATS = [], CAT_NAME = {}, CAT_ABOUT = {}, CAT_COLOR = {};
const CAT_LETTER = { m: "make", w: "watch", t: "talk", p: "play", b: "browse", o: "other" };
function setCategories(defs) {
  if (!Array.isArray(defs) || !defs.length) return;
  CAT_DEFS = defs;
  CATS = defs.map((c) => c.id);
  CAT_NAME = Object.fromEntries(defs.map((c) => [c.id, c.name]));
  CAT_ABOUT = Object.fromEntries(defs.map((c) => [c.id, c.about || ""]));
  CAT_COLOR = Object.fromEntries(defs.map((c) => [c.id, c.color]));
}
setCategories(DEFAULT_CATS);
const catName = (c) => CAT_NAME[c] || "Other";
const catColor = (c) => CAT_COLOR[c] || CAT_COLOR.other || "#575757";
const catStyle = (c) => `--c:${catColor(c)}`;
const LDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]; // the server's 0 is Monday
const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
const pad2 = (n) => String(n).padStart(2, "0");
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** 4h 05m, 42m, 30s */
function dur(s) {
  s = Math.round(s || 0);
  const m = Math.floor(s / 60), h = Math.floor(m / 60);
  if (h) return `${h}h ${pad2(m % 60)}m`;
  if (m) return `${m}m`;
  return s ? `${s}s` : "0m";
}
const parseDay = (d) => { const [y, m, dd] = d.split("-").map(Number); return new Date(y, m - 1, dd); };
const shiftDay = (d, n) => isoDate(addDays(parseDay(d), n));
const dayName = (d, today) => d === today ? "Today" : d === shiftDay(today, -1) ? "Yesterday"
  : dateFmt(parseDay(d), { weekday: "short", day: "numeric", month: "short" });
const hhmm = (ms) => { const t = new Date(ms); return `${pad2(t.getHours())}:${pad2(t.getMinutes())}`; };
function whenText(ms) {
  const t = new Date(ms), days = Math.round((new Date(t).setHours(0, 0, 0, 0) - startOfToday()) / DAY);
  if (days <= 0) return hhmm(ms);
  if (days === 1) return `tomorrow ${hhmm(ms)}`;
  return `${t.toLocaleDateString(undefined, { weekday: "short" })} ${hhmm(ms)}`;
}
const keyDot = (cat) => `<i class="key c-${esc(cat)}" style="${esc(catStyle(cat))}"></i>`;

const sc = {
  view: ["day", "weeks", "apps"].includes(ls.getItem("ignition.scView")) ? ls.getItem("ignition.scView") : "day",
  days: [7, 28, 91].includes(Number(ls.getItem("ignition.scDays"))) ? Number(ls.getItem("ignition.scDays")) : 28,
  date: null, device: null, day: null, range: null, devices: null, all: false, error: null, drawnKey: "",
};
const lim = { data: null, error: null };

/* ---- loading ---- */

async function loadScreen(what = "all") {
  const dev = sc.device ? `&device=${encodeURIComponent(sc.device)}` : "";
  try {
    const jobs = [];
    if (what !== "range") {
      jobs.push(api("GET", `/api/day?${sc.date ? `date=${sc.date}` : ""}${dev}`).then((d) => {
        setCategories(d.category_defs);
        sc.day = d;
        if (!sc.date) sc.date = d.date;
        sc.day._byKey = Object.fromEntries((d.entities || []).map((e) => [e.key, e]));
      }));
    }
    if (what !== "day") jobs.push(api("GET", `/api/range?days=${sc.days}${sc.date ? `&end=${sc.date}` : ""}${dev}`).then((r) => { setCategories(r.category_defs); sc.range = r; }));
    await Promise.all(jobs);
    sc.error = null;
  } catch (e) {
    sc.error = e.message;
  }
  if (state.page === "screen") renderScreen();
}

async function loadLimits() {
  try {
    lim.data = await api("GET", "/api/limits");
    setCategories(lim.data.category_defs);
    lim.error = null;
  } catch (e) {
    lim.error = e.message;
  }
  if (state.page === "limits") renderLimits();
  paintLimitsTab();
}

async function loadToday() {
  try {
    const t = await api("GET", "/api/screen/today");
    const a = $("#screen-today");
    a.hidden = false;
    a.innerHTML = `<span>Screen today</span><b>${esc(dur(t.lit))}</b>`;
  } catch { $("#screen-today").hidden = true; }
}

/* ---- the Screen tab ---- */

function renderScreen() {
  $$("#sc-view button").forEach((b) => b.classList.toggle("on", b.dataset.view === sc.view));
  $$("#sc-days button").forEach((b) => b.classList.toggle("on", Number(b.dataset.days) === sc.days));
  const d = sc.day;
  if (!d) {
    $("#screen").hidden = true;
    $("#sc-nav").hidden = true;
    $("#sc-empty").hidden = false;
    $("#sc-empty").textContent = sc.error ? "Screen time needs the server" : "Loading";
    return;
  }
  $("#sc-nav").hidden = false;
  $("#sc-empty").hidden = true;
  $("#screen").hidden = false;
  const day = sc.view === "day";
  $("#sc-prev").hidden = $("#sc-next").hidden = !day;
  $("#sc-days").hidden = day;
  $("#sc-when").textContent = day ? dayName(d.date, d.today) : `${sc.days} days to ${dayName(sc.date, d.today).toLowerCase()}`;
  $("#sc-next").disabled = d.date >= d.today;
  hideTip();
  if (day) renderDay();
  else if (sc.range) sc.view === "weeks" ? renderWeeks() : renderAppsView();
}

function who(list, allSeconds) {
  const shown = list.filter((x) => x.seconds > 0 || x.id === sc.device);
  const host = $("#sc-who");
  if (shown.length < 2 && !sc.device) { host.innerHTML = ""; return; }
  host.innerHTML = `<button type="button" class="${sc.device ? "" : "on"}" data-dev="">All <b>${dur(allSeconds)}</b></button>` +
    shown.map((x) => `<button type="button" class="${sc.device === x.id ? "on" : ""}" data-dev="${esc(x.id)}">${esc(x.name)} <b>${dur(x.seconds)}</b></button>`).join("");
}
$("#sc-who").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-dev]");
  if (!b) return;
  sc.device = b.dataset.dev || null;
  sc.all = false;
  loadScreen();
});

const dayTotal = (d) => (d.has_data ? d.lit : 0) + (d.legacy ? d.legacy.seconds : 0);
const clock = (d, off) => { const t = new Date((d.t0 + off + d.utc_offset) * 1000); return `${pad2(t.getUTCHours())}:${pad2(t.getUTCMinutes())}`; };

function heroTotal(sec, key) {
  const el = $("#sc-total");
  if (sc.drawnKey !== key) {
    sc.drawnKey = key;
    FX.countUp(el, sec, (v) => dur(Math.round(v / 60) * 60));
    FX.replay($("#screen"), "drawn");
  } else {
    $("#screen").classList.remove("drawn");
    el.textContent = dur(sec);
  }
}

function renderDay() {
  const d = sc.day, u = d.usual || {};
  heroTotal(dayTotal(d), `day|${d.date}|${sc.device}`);
  let sub = "";
  if (!d.has_data && d.legacy) sub = "From an older tracker";
  else if (!d.has_data) sub = d.in_progress ? "Nothing yet" : "Nothing recorded";
  else if (u.enough) {
    const base = d.in_progress ? u.lit_by_now : u.lit;
    const diff = d.lit - base;
    sub = Math.abs(diff) < Math.max(600, base * 0.1) ? "About usual" : `${dur(Math.abs(diff))} ${diff > 0 ? "more" : "less"} than usual`;
  } else if (d.in_progress) sub = "So far today";
  const notes = (d.notes || []).map((n) => `${n.name} silent since ${clock(d, n.since)}`);
  $("#sc-sub").textContent = [sub, ...notes].filter(Boolean).join(" · ");
  who(d.devices || [], dayTotal(d));

  const host = $("#sc-chart");
  host.hidden = !d.has_data;
  if (d.has_data && host.clientWidth > 100) host.innerHTML = dayChart(d, host.clientWidth);
  legend(d.categories, d.burns && d.burns.length);
  facts(d);
  const rows = mergeApps(d.has_data ? d.entities : [], d.legacy && d.legacy.apps);
  things(rows, 10, "this day");
}

function dayChart(d, W) {
  const span = d.t1 - d.t0, x = (off) => (off / span) * W;
  const small = W < 560, H = small ? 120 : 150, top = 18, base = top + H;
  const strips = d.strips || [], stripGap = 34, stripH = 12, stripsTop = base + 48;
  const height = strips.length ? stripsTop + (strips.length - 1) * stripGap + stripH + 4 : base + 26;
  let out = "";
  for (const b of d.burns || []) {
    const a = Math.max(0, b.start), e = Math.min(span, b.live ? Math.max(b.start, d.now - d.t0) : b.end);
    if (e <= a) continue;
    out += `<rect class="burn" x="${x(a)}" y="${top - 6}" width="${Math.max(2, x(e) - x(a))}" height="${height - top + 6}"/>`;
  }
  out += `<line class="grid" x1="0" x2="${W}" y1="${top}" y2="${top}"/><text x="${W}" y="${top - 5}" text-anchor="end">60m</text>`;
  out += `<line class="grid" x1="0" x2="${W}" y1="${top + H / 2}" y2="${top + H / 2}"/><text x="${W}" y="${top + H / 2 - 5}" text-anchor="end">30m</text>`;
  out += `<line class="base" x1="0" x2="${W}" y1="${base}" y2="${base}"/>`;
  const step = small ? 6 : W < 900 ? 3 : 2;
  for (let hr = 0; hr <= 24; hr += step) {
    out += `<text x="${x(hr * 3600)}" y="${base + 16}" text-anchor="${hr === 0 ? "start" : hr === 24 ? "end" : "middle"}">${clock(d, hr * 3600)}</text>`;
  }
  // One column an hour, stacked good to bad from the bottom.
  const n = d.curve.length, binS = span / n, per = Math.round(3600 / binS);
  const hours = [];
  for (let i = 0; i < n; i += per) {
    let lit = 0; const cats = {}, tops = {};
    for (let j = i; j < Math.min(n, i + per); j++) {
      const c = d.curve[j];
      if (!c) continue;
      lit += c.lit * binS;
      for (const [k, v] of Object.entries(c.cats)) cats[k] = (cats[k] || 0) + v;
      for (const [label, v] of c.top) tops[label] = (tops[label] || 0) + v;
    }
    hours.push({ from: i * binS, to: Math.min(n, i + per) * binS, lit: Math.min(lit, 3600), cats, top: Object.entries(tops).sort((a, b) => b[1] - a[1]).slice(0, 3) });
  }
  const cw = W / hours.length, gap = small ? 2 : 5;
  hours.forEach((h, i) => {
    const sub = `${clock(d, h.from)} to ${clock(d, h.to)}` + (h.top.length ? ` · ${h.top.map(([l]) => l).join(", ")}` : "");
    out += `<g class="mark" style="--i:${i}" data-tip="${esc(dur(h.lit))}" data-sub="${esc(sub)}"><rect class="hit" x="${i * cw}" y="${top}" width="${cw}" height="${H}"/>`;
    const ordered = [...new Set([...CATS, ...Object.keys(h.cats)])];
    const sum = ordered.reduce((a, c) => a + (h.cats[c] || 0), 0) || 1;
    let y = base;
    const full = (h.lit / 3600) * H;
    for (const c of ordered) {
      const part = ((h.cats[c] || 0) / sum) * full;
      if (part <= 0) continue;
      const hh = Math.max(1, part - (part > 4 ? 1.5 : 0));
      out += `<rect class="c-${esc(c)}" style="${esc(catStyle(c))}" x="${i * cw + gap / 2}" y="${y - part}" width="${Math.max(2, cw - gap)}" height="${hh}" rx="${Math.min(2, hh / 2)}"/>`;
      y -= part;
    }
    out += "</g>";
  });
  strips.forEach((st, k) => {
    const y = stripsTop + k * stripGap;
    const dev = (d.devices || []).find((v) => v.id === st.device) || { name: st.device };
    out += `<text class="name" x="0" y="${y - 6}">${esc(dev.name)}</text><text x="${W}" y="${y - 6}" text-anchor="end">${dur(st.seconds)}</text>`;
    out += `<rect class="track" x="0" y="${y}" width="${W}" height="${stripH}" rx="2"/>`;
    for (const [a, b, key] of st.spans) {
      const e = sc.day._byKey[key] || { label: key.slice(2), cat: "other" };
      out += `<rect class="c-${esc(e.cat)} mark" style="${esc(catStyle(e.cat))}" x="${x(a)}" y="${y}" width="${Math.max(0.8, x(b) - x(a))}" height="${stripH}" data-tip="${esc(e.label)}" data-sub="${esc(`${clock(d, a)} to ${clock(d, b)} · ${dur(b - a)}`)}"/>`;
    }
  });
  if (d.in_progress) { const nx = x(d.now - d.t0); out += `<line class="now" x1="${nx}" x2="${nx}" y1="${top - 4}" y2="${height}"/>`; }
  return `<svg viewBox="0 0 ${W} ${height}" width="${W}" height="${height}" role="img" aria-label="Screen time by hour">${out}</svg>`;
}

// Most time first, in words: the colours alone don't say which is which.
function legend(cats, burns) {
  const order = [...new Set([...CATS, ...Object.keys(cats || {})])].filter((c) => cats && cats[c] > 0)
    .sort((a, b) => cats[b] - cats[a]);
  $("#sc-legend").innerHTML = order.map((c) => `<span title="${esc(CAT_ABOUT[c] || catName(c))}">${keyDot(c)}${esc(catName(c))} <b>${dur(cats[c])}</b></span>`).join("")
    + (burns ? `<span><i class="key burn"></i>Burn</span>` : "");
}

function facts(d) {
  const a = d.attention || {}, u = d.usual || {}, out = [];
  const add = (label, value, usual) => out.push(`<div><dt>${label}</dt><dd>${esc(value)}${u.enough && usual != null ? `<small>usually ${esc(usual)}</small>` : ""}</dd></div>`);
  if (d.has_data) {
    if (a.longest_sitting) add("Longest stretch", dur(a.longest_sitting.seconds), u.longest_sitting != null ? dur(u.longest_sitting) : null);
    if ((d.devices || []).some((v) => v.kind === "phone") && a.pickups != null) add("Pickups", String(a.pickups), u.pickups != null ? String(Math.round(u.pickups)) : null);
    if (d.first && d.first.at != null) add("First", clock(d, d.first.at), u.first != null ? clock(d, u.first) : null);
    if (d.last && d.last.at != null && !d.in_progress) add("Last", clock(d, d.last.at), u.last != null ? clock(d, u.last) : null);
  }
  $("#sc-facts").innerHTML = out.join("");
}

/** Recorded apps, plus totals from an older tracker folded in by name. */
function mergeApps(entities, legacyApps) {
  const rows = (entities || []).map((e) => ({ ...e }));
  const by = Object.fromEntries(rows.map((e) => [e.label.toLowerCase(), e]));
  for (const [label, secs, cat] of legacyApps || []) {
    const hit = by[label.toLowerCase()];
    if (hit) hit.seconds += secs;
    else rows.push({ key: `n:${label.toLowerCase()}`, label, cat, seconds: secs });
  }
  return rows.sort((a, b) => b.seconds - a.seconds);
}

let thingRows = [];
function things(rows, limit, scope) {
  thingRows = rows;
  const max = rows[0] ? rows[0].seconds : 1;
  const shown = sc.all ? rows : rows.slice(0, limit);
  $("#sc-count").textContent = rows.length ? String(rows.length) : "";
  $("#sc-things").innerHTML = shown.map((e, i) => `<li><button type="button" class="thing" data-i="${i}" data-scope="${esc(scope)}" aria-label="${esc(`${e.label}, ${dur(e.seconds)}, ${catName(e.cat)}`)}">
    <span class="nm">${keyDot(e.cat)}<span class="label">${esc(e.label)}</span>${e.blocked ? `<span class="tag-blocked">Blocked</span>` : ""}</span>
    <span class="bar"><i class="c-${esc(e.cat)}" style="${esc(catStyle(e.cat))};width:${Math.max(1, (e.seconds / max) * 100)}%"></i></span>
    <span class="time">${dur(e.seconds)}</span></button></li>`).join("");
  $("#sc-more").hidden = rows.length <= limit;
  $("#sc-more").textContent = sc.all ? "Show fewer" : `Show all ${rows.length}`;
}
$("#sc-more").onclick = () => { sc.all = !sc.all; renderScreen(); };

/* ---- what an app counts as ---- */
const thingDialog = $("#thing-dialog");
let thingOpen = null;
$("#sc-things").addEventListener("click", (e) => {
  const b = e.target.closest(".thing");
  if (!b) return;
  const t = thingRows[Number(b.dataset.i)];
  if (!t || !t.key) return;
  thingOpen = t;
  $("#thing-title").textContent = t.label;
  $("#thing-sub").textContent = `${dur(t.seconds)}, ${b.dataset.scope}`;
  $("#thing-cats").innerHTML = CATS.map((c) => `<button type="button" data-cat="${esc(c)}" class="${c === t.cat ? "on" : ""}" title="${esc(CAT_ABOUT[c] || catName(c))}">${keyDot(c)}${esc(catName(c))}</button>`).join("")
    + `<button type="button" data-cat="">Automatic</button>`;
  thingDialog.showModal();
});
$("#thing-cats").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-cat]");
  if (!b || !thingOpen) return;
  try {
    const r = await api("PUT", "/api/category", { key: thingOpen.key, cat: b.dataset.cat });
    thingDialog.close();
    // Out of a kind a limit covers is a loosening: it waits like one.
    const name = b.dataset.cat ? catName(b.dataset.cat) : "Automatic";
    toast(r.applied === false ? `${thingOpen.label} counts as ${name} from ${whenText(r.at)}`
      : `${thingOpen.label} counts as ${name}`);
    loadScreen();
  } catch (err) { toast(err.message); }
});
$("#thing-close").onclick = () => thingDialog.close();

/* ---- custom categories ---- */
const categoryDialog = $("#category-dialog");
let categoryDraft = [];
function openCategories() {
  categoryDraft = CAT_DEFS.map((c) => ({ id: c.id, name: c.name, color: c.color, custom: c.custom }));
  $("#category-msg").textContent = "";
  drawCategories();
  categoryDialog.showModal();
}
function drawCategories() {
  $("#category-list").innerHTML = categoryDraft.map((c, i) => `<li data-i="${i}">
    <input type="color" data-field="color" value="${esc(c.color)}" aria-label="${esc(c.name || "New category")} colour">
    <input type="text" data-field="name" value="${esc(c.name)}" maxlength="32" placeholder="Category name" aria-label="Category name">
    ${c.custom ? `<button type="button" class="tag-x" data-del="${i}" aria-label="Delete ${esc(c.name || "category")}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17"/></svg></button>` : ""}
  </li>`).join("");
}
$("#sc-categories").onclick = openCategories;
$("#thing-edit-cats").onclick = () => { thingDialog.close(); openCategories(); };
$("#category-close").onclick = () => categoryDialog.close();
$("#category-add").onclick = () => {
  if (categoryDraft.length >= 32) { $("#category-msg").textContent = "Keep at most 32 categories."; return; }
  categoryDraft.push({ id: "", name: "", color: "#e23a29", custom: true });
  $("#category-msg").textContent = "";
  drawCategories();
  const inputs = $$("#category-list input[type=text]");
  inputs[inputs.length - 1].focus();
};
$("#category-list").addEventListener("input", (e) => {
  const row = e.target.closest("li[data-i]"), field = e.target.dataset.field;
  if (!row || !field) return;
  categoryDraft[Number(row.dataset.i)][field] = e.target.value;
});
$("#category-list").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-del]");
  if (!b) return;
  categoryDraft.splice(Number(b.dataset.del), 1);
  drawCategories();
});
$("#category-save").onclick = async () => {
  const b = $("#category-save");
  b.disabled = true;
  try {
    const r = await api("PUT", "/api/categories", { categories: categoryDraft.map(({ id, name, color }) => ({ id, name, color })) });
    setCategories(r.categories);
    categoryDialog.close();
    toast("Categories saved");
    loadScreen();
    loadLimits();
  } catch (err) { $("#category-msg").textContent = err.message; }
  finally { b.disabled = false; }
};

/* ---- weeks: every day as a row, coloured by what each 10 minutes went to ---- */
function renderWeeks() {
  const r = sc.range;
  heroTotal(r.average, `weeks|${sc.days}|${sc.date}|${sc.device}`);
  $("#sc-sub").textContent = "A day, on average";
  who(r.devices || [], (r.devices || []).reduce((a, x) => a + x.seconds, 0));
  legend(r.categories, (r._burns || []).length);
  $("#sc-facts").innerHTML = "";
  const host = $("#sc-chart");
  host.hidden = false;
  host.innerHTML = "";
  const W = Math.max(280, Math.floor(host.clientWidth));
  const small = W < 560, left = small ? 50 : 72, right = small ? 48 : 62;
  const rowH = r.days <= 7 ? 24 : r.days <= 31 ? 14 : 6, gap = r.days <= 31 ? 3 : 2;
  const rows = r.rows.slice().reverse(), raster = r.raster.slice().reverse();
  let y = 0; const ys = [];
  rows.forEach((row) => { ys.push(y); y += rowH + gap + (row.weekday === 0 ? (r.days <= 31 ? 7 : 4) : 0); });
  const height = y + 18;
  const dpr = Math.min(2, devicePixelRatio || 1);
  const cv = document.createElement("canvas");
  cv.width = W * dpr; cv.height = height * dpr; cv.style.height = `${height}px`; cv.style.width = `${W}px`;
  cv.setAttribute("role", "img");
  cv.setAttribute("aria-label", `Screen time by time of day, ${r.days} days`);
  const g = cv.getContext("2d");
  g.scale(dpr, dpr);
  const col = Object.fromEntries(CATS.map((c) => [c, catColor(c)]));
  const text = "#fff", muted = cssVar("--muted"), faint = cssVar("--faint"), well = cssVar("--surface-2");
  const plotW = W - left - right, maxLit = Math.max(1, ...rows.map((x) => x.lit));
  g.textBaseline = "middle";
  rows.forEach((row, i) => {
    const ry = ys[i], ras = raster[i], nb = ras.lit.length, cw = plotW / nb, sel = row.date === sc.date;
    g.globalAlpha = 1; g.fillStyle = well; g.fillRect(left, ry, plotW, rowH);
    for (let b = 0; b < nb; b++) {
      const ch = ras.lit[b];
      if (ch === ".") continue;
      g.globalAlpha = 0.35 + 0.65 * ((Number(ch) + 1) / 10);
      const cat = ras.cat_ids ? ras.cat_ids[b] : CAT_LETTER[ras.cat[b]];
      g.fillStyle = col[cat] || catColor(cat);
      g.fillRect(left + b * cw, ry, Math.ceil(cw * 10) / 10 + 0.3, rowH);
    }
    g.globalAlpha = 1;
    const dt = parseDay(row.date);
    if (r.days <= 31 || row.weekday === 0 || sel) {
      g.font = `${r.days <= 31 ? 11 : 10}px "Space Mono", monospace`;
      g.fillStyle = sel ? text : row.weekday >= 5 ? muted : faint;
      g.textAlign = "left";
      g.fillText(dt.toLocaleDateString(undefined, small || r.days > 31 ? { day: "numeric", month: "short" } : { weekday: "short", day: "numeric" }), 0, ry + rowH / 2 + 0.5);
    }
    if (sel) { g.fillStyle = cssVar("--accent"); g.fillRect(left - 5, ry, 2, rowH); }
    if (row.lit > 0) {
      if (r.days <= 31) {
        g.textAlign = "right"; g.font = `11px "Space Mono", monospace`; g.fillStyle = sel ? text : muted;
        g.fillText(dur(row.lit), W, ry + rowH / 2 + 0.5);
      } else {
        g.fillStyle = muted;
        g.fillRect(left + plotW + 8, ry + 1, Math.max(1, (row.lit / maxLit) * (right - 10)), Math.max(1, rowH - 2));
      }
    }
  });
  g.font = `10px "Space Mono", monospace`; g.fillStyle = faint;
  for (let hr = 0; hr <= 24; hr += small ? 6 : 3) {
    g.textAlign = hr === 0 ? "left" : hr === 24 ? "right" : "center";
    g.fillText(`${pad2((hr + 4) % 24)}:00`, left + (hr / 24) * plotW, height - 7);
  }
  host.append(cv);
  const at = (ev) => {
    const box = cv.getBoundingClientRect(), py = ev.clientY - box.top, px = ev.clientX - box.left;
    const i = ys.findIndex((yy) => py >= yy && py < yy + rowH + gap);
    return i < 0 ? null : rows[i];
  };
  cv.addEventListener("click", (ev) => {
    const row = at(ev);
    if (!row) return;
    sc.date = row.date; sc.view = "day"; sc.all = false;
    ls.setItem("ignition.scView", "day");
    loadScreen();
  });
  const apps = mergeApps(r.census, r.legacy_apps);
  things(apps, 10, `${r.days} days`);
}

function renderAppsView() {
  const r = sc.range;
  const rows = mergeApps(r.census, r.legacy_apps);
  const total = rows.reduce((a, e) => a + e.seconds, 0);
  heroTotal(total, `apps|${sc.days}|${sc.date}|${sc.device}`);
  $("#sc-sub").textContent = `${rows.length} ${rows.length === 1 ? "app or site" : "apps and sites"}`;
  who(r.devices || [], (r.devices || []).reduce((a, x) => a + x.seconds, 0));
  const cats = Object.fromEntries(CATS.map((c) => [c, 0]));
  for (const e of rows) cats[e.cat] = (cats[e.cat] || 0) + e.seconds;
  const host = $("#sc-chart");
  host.hidden = !total;
  const ordered = [...new Set([...CATS, ...Object.keys(cats)])].filter((c) => cats[c] > 0);
  host.innerHTML = total ? `<div class="splitbar" role="img" aria-label="${esc(ordered.map((c) => `${catName(c)} ${Math.round((cats[c] / total) * 100)}%`).join(", "))}">`
    + ordered.map((c) => `<i class="c-${esc(c)} mark" style="${esc(catStyle(c))};flex:${cats[c] / total} 1 0" data-tip="${Math.round((cats[c] / total) * 100)}%" data-sub="${esc(catName(c))}, ${esc(dur(cats[c]))}"></i>`).join("") + "</div>" : "";
  legend(cats);
  $("#sc-facts").innerHTML = "";
  things(rows, 15, `${r.days} days`);
}

$$("#sc-view button").forEach((b) => b.onclick = () => {
  sc.view = b.dataset.view; sc.all = false;
  ls.setItem("ignition.scView", sc.view);
  renderScreen();
  if (sc.view !== "day" && !sc.range) loadScreen("range");
});
$$("#sc-days button").forEach((b) => b.onclick = () => {
  sc.days = Number(b.dataset.days); sc.all = false;
  ls.setItem("ignition.scDays", sc.days);
  loadScreen("range");
});
$("#sc-prev").onclick = () => { sc.date = shiftDay(sc.date, -1); sc.all = false; loadScreen(); };
$("#sc-next").onclick = () => { if (sc.day && sc.date < sc.day.today) { sc.date = shiftDay(sc.date, 1); sc.all = false; loadScreen(); } };

for (const el of [$("#sc-chart")]) {
  el.addEventListener("pointerover", (e) => { const m = e.target.closest("[data-tip]"); if (m) showTip(m); });
  el.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") hideTip(); });
}

/* ---- the Limits tab ---- */

const RULE_KINDS = { day: "Time per day", every: "Time per X hours", block: "Blocked hours" };
const RULE_DEFAULTS = {
  day: { minutes: 60 }, every: { minutes: 30, hours: 4 }, block: { from: "22:00", to: "07:00" },
};
const DEFAULT_UNLOCK = { hold: 5, wait: 3, grant: 10, per_day: null };

function minText(m) {
  if (m < 60) return `${m} min`;
  return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`;
}
function daysText(days) {
  if (!days || days.length === 7) return "";
  const s = days.join("");
  if (s === "01234") return " on weekdays";
  if (s === "56") return " on weekends";
  return " on " + days.map((d) => LDAYS[d].slice(0, 3)).join(", ");
}
function ruleText(r) {
  const on = daysText(r.days);
  if (r.kind === "day") return r.minutes ? `Up to ${minText(r.minutes)} a day${on}` : `Blocked all day${on}`;
  if (r.kind === "every") return `Up to ${minText(r.minutes)} every ${r.hours === 1 ? "hour" : `${r.hours} hours`}`;
  return r.from === r.to ? `Blocked all day${on}` : `Blocked from ${r.from} to ${r.to}${on}`;
}
const rulesText = (rules) => rules.map((r, i) => { const t = ruleText(r); return i ? t[0].toLowerCase() + t.slice(1) : t; }).join(", ");

/** The answer in words: blocked, unlocked, or how much is left. */
function limitStatus(item) {
  const s = item.state, u = item.unlock || {};
  if (u.open_until) return { word: "Unlocked", sub: `until ${whenText(u.open_until)}`, cls: "unlocked" };
  if (s.blocked) return { word: "Blocked", sub: s.until ? `until ${whenText(s.until * 1000)}` : "", cls: "closed" };
  if (s.left != null) return { word: "Allowed", sub: `${dur(s.left)} left`, cls: "open" };
  return { word: "Allowed", sub: "", cls: "open" };
}

function paintLimitsTab() {
  const n = lim.data ? lim.data.limits.filter((i) => i.state.blocked && !(i.unlock || {}).open_until).length : 0;
  $("#tab-limits-label").textContent = "Limits";
  $(`.tabs a[data-tab="limits"]`).classList.toggle("closed", n > 0);
}

let unlockTimer = 0;
function renderLimits() {
  const L = lim.data;
  clearTimeout(unlockTimer);
  if (!L) {
    $("#lim-list").innerHTML = "";
    $("#lim-settings").hidden = true;
    $("#lim-new").hidden = true;
    $("#lim-empty").hidden = false;
    $("#lim-empty").textContent = lim.error ? "Limits need the server" : "Loading";
    return;
  }
  $("#lim-new").hidden = false;
  const burning = L.burn && L.burn.phase === "burning";
  $("#lim-burn").hidden = !burning || !L.limits.some((i) => i.limit.unlock);
  $("#lim-empty").hidden = L.limits.length > 0;
  $("#lim-empty").textContent = "No limits yet";
  const now = Date.now();
  $("#lim-list").innerHTML = L.limits.map((item, i) => {
    const st = limitStatus(item), l = item.limit;
    const tight = item.state.rules.filter((r) => r.active && r.allowed).sort((a, b) => (a.allowed - a.used) - (b.allowed - b.used))[0];
    const bar = tight ? `<span class="lim-bar" title="${esc(`${dur(tight.used)} of ${dur(tight.allowed)}`)}"><i class="${tight.used >= tight.allowed ? "over" : ""}" style="width:${Math.min(100, (tight.used / tight.allowed) * 100)}%"></i></span>
      <span class="lim-used">${dur(tight.used)} of ${dur(tight.allowed)}</span>` : "";
    const pending = item.pending_at ? `<span class="lim-pending">${item.deleting ? "Deletes" : "Changes"} at ${esc(whenText(item.pending_at))}</span>` : "";
    return `<li class="limit ${st.cls}" data-i="${i}">
      <button type="button" class="lim-main" data-edit="${i}" aria-label="${esc(`${item.label}: ${[st.word, st.sub].filter(Boolean).join(" ")}. Edit`)}">
        <span class="lim-name">${esc(item.label)}</span>
        <span class="lim-state"><b>${st.word}</b> ${esc(st.sub)}</span>
        <span class="lim-rules">${esc(rulesText(l.rules))}</span>
        ${bar}${pending}
      </button>
      ${unlockArea(item, i, burning, now)}
    </li>`;
  }).join("");
  wireUnlocks();
  renderLimitSettings(L);
  // Tick the waits down, and redraw when one is ready.
  const waits = L.limits.map((i) => (i.unlock || {}).pending).filter(Boolean);
  if (waits.length) unlockTimer = setTimeout(() => { if (state.page === "limits") renderLimits(); }, 1000);
}

function unlockArea(item, i, burning, now) {
  const l = item.limit, u = item.unlock || {};
  if (!l.unlock || !item.state.blocked || u.open_until || burning) return "";
  const left = u.left == null ? "" : `${u.left} of ${u.per_day} unlocks left today`;
  if (u.left === 0) return `<div class="lim-unlock"><span class="lim-note">No unlocks left today</span></div>`;
  if (u.pending) {
    const ms = u.pending.ready - now;
    if (ms > 0) {
      return `<div class="lim-unlock"><span class="lim-wait"><b>${fmtClock(ms / 1000)}</b> until you can unlock it</span>
        <button type="button" class="ghost small" data-cancel="${i}">Cancel</button></div>`;
    }
    return `<div class="lim-unlock"><button type="button" class="primary" data-open="${i}">Unlock for ${minText(l.unlock.grant)}</button><span class="lim-note">${esc(left)}</span></div>`;
  }
  const wait = l.unlock.wait ? `, then wait ${minText(l.unlock.wait)}` : "";
  return `<div class="lim-unlock"><button type="button" class="hold-btn" data-hold="${i}" style="--hold:${l.unlock.hold}s"><span class="hold-fill"></span><span class="hold-text">Hold to unlock</span></button>
    <span class="lim-note">${esc(`Hold for ${l.unlock.hold} s${wait}. ${left}`)}</span></div>`;
}

/** A button that only acts once it has been held down for `seconds`. Letting go early starts it over. */
function bindHold(btn, seconds, onDone) {
  let t = 0;
  const cancel = () => { clearTimeout(t); t = 0; btn.classList.remove("holding"); };
  const begin = () => {
    if (t || btn.disabled) return;
    btn.classList.add("holding");
    t = setTimeout(() => { t = 0; btn.classList.remove("holding"); onDone(); }, seconds * 1000);
  };
  btn.addEventListener("pointerdown", (e) => { if (e.button) return; try { btn.setPointerCapture(e.pointerId); } catch { /* not a live pointer */ } begin(); });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) btn.addEventListener(ev, cancel);
  btn.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); begin(); } });
  btn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") cancel(); });
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
}

const unlockStep = async (item, body) => {
  try {
    const r = await api("POST", `/api/limits/${encodeURIComponent(item.limit.id)}/unlock`, body);
    await loadLimits();
    return r;
  } catch (e) { toast(e.message); loadLimits(); return null; }
};

function wireUnlocks() {
  const items = lim.data.limits;
  $$("#lim-list [data-edit]").forEach((b) => b.onclick = () => openLimit(items[Number(b.dataset.edit)]));
  $$("#lim-list [data-hold]").forEach((b) => {
    const item = items[Number(b.dataset.hold)];
    bindHold(b, item.limit.unlock.hold, async () => {
      const r = await unlockStep(item, { step: "start", device: deviceTag() });
      if (r && r.ready - r.now <= 2000) unlockStep(item, { step: "open", id: r.id });
    });
  });
  $$("#lim-list [data-open]").forEach((b) => b.onclick = async () => {
    const item = items[Number(b.dataset.open)];
    b.disabled = true;
    const r = await unlockStep(item, { step: "open", id: item.unlock.pending.id });
    if (r) toast(`${item.label} is unlocked until ${whenText(r.until)}`);
  });
  $$("#lim-list [data-cancel]").forEach((b) => b.onclick = () => {
    const item = items[Number(b.dataset.cancel)];
    unlockStep(item, { step: "cancel", id: item.unlock.pending.id });
  });
}
const deviceTag = () => (NATIVE ? "phone" : "web");

/* the changes box: how long a loosening waits, and the phone's guard */
function renderLimitSettings(L) {
  $("#lim-settings").hidden = false;
  const s = L.settings;
  const w = $("#lim-wait");
  if (document.activeElement !== w && !limSetDirty) w.value = s.changes_wait;
  if (!limSetDirty) $("#lim-guard").checked = !!s.guard;
  $("#lim-set-note").textContent = L.settings_pending && L.settings_at ? `Takes effect at ${whenText(L.settings_at)}` : "";
  const box = $("#lim-set-actions");
  if (!limSetDirty) {
    box.innerHTML = L.settings_pending ? `<button type="button" class="ghost small" id="lim-set-keep">Cancel change</button>` : "";
    const keep = $("#lim-set-keep");
    if (keep) keep.onclick = async () => { await api("PUT", "/api/limits/settings", { cancel: true }); loadLimits(); };
  }
}
let limSetDirty = false;
const limSetBody = () => ({ changes_wait: Math.max(0, Math.min(1440, parseInt($("#lim-wait").value, 10) || 0)), guard: $("#lim-guard").checked });
async function limSetChanged() {
  limSetDirty = true;
  const box = $("#lim-set-actions");
  try {
    const dry = await api("PUT", "/api/limits/settings", { ...limSetBody(), dry: true });
    if (!dry.wait) { await saveLimSettings(); return; }
    box.innerHTML = `<button type="button" class="hold-btn" id="lim-set-save" style="--hold:5s"><span class="hold-fill"></span><span class="hold-text">Hold to save</span></button>
      <button type="button" class="ghost small" id="lim-set-undo">Undo</button><span class="lim-note">This loosens your limits, so it takes effect in ${minText(dry.minutes)}</span>`;
    bindHold($("#lim-set-save"), 5, saveLimSettings);
    $("#lim-set-undo").onclick = () => { limSetDirty = false; renderLimitSettings(lim.data); };
  } catch (e) { toast(e.message); }
}
async function saveLimSettings() {
  try {
    const r = await api("PUT", "/api/limits/settings", limSetBody());
    limSetDirty = false;
    if (r.applied === false) toast(`Takes effect at ${whenText(r.at)}`);
    loadLimits();
  } catch (e) { toast(e.message); }
}
$("#lim-wait").addEventListener("change", limSetChanged);
$("#lim-guard").addEventListener("change", limSetChanged);

/* ---- the limit editor ---- */
const limitDialog = $("#limit-dialog");
let draft = null, editing = null;

function newRule(kind) {
  return kind === "every" ? { kind, ...RULE_DEFAULTS[kind] } : { kind, ...RULE_DEFAULTS[kind], days: [...ALL_DAYS] };
}

async function openLimit(item) {
  editing = item;
  // The limit in force, not a change that's waiting: a save is measured against it.
  draft = item ? JSON.parse(JSON.stringify(item.limit)) : { targets: [], labels: {}, name: "", rules: [newRule("block")], unlock: { ...DEFAULT_UNLOCK } };
  draft.labels = draft.labels || {};
  $("#lim-title").textContent = item ? item.label : "New limit";
  $("#lim-name").value = draft.name || "";
  $("#lim-msg").textContent = item && item.pending_at ? (item.deleting ? `Will be deleted at ${whenText(item.pending_at)}` : `A change takes effect at ${whenText(item.pending_at)}. Saving replaces it.`) : "";
  $("#lim-msg").classList.remove("error");
  const u = draft.unlock || { ...DEFAULT_UNLOCK };
  $("#lim-unlock-on").checked = !!draft.unlock;
  $("#lim-hold").value = u.hold; $("#lim-uwait").value = u.wait; $("#lim-grant").value = u.grant; $("#lim-perday").value = u.per_day || "";
  $("#lim-unlock").classList.toggle("off", !draft.unlock);
  drawTargets();
  drawRules();
  drawActions();
  limitDialog.showModal();
  if (!sc.range) { await loadScreen("range"); }
  $("#lim-known").innerHTML = (sc.range ? sc.range.census : []).filter((e) => e.key).slice(0, 300).map((e) => `<option value="${esc(e.label)}">`).join("");
}

function drawTargets() {
  $("#lim-cats").innerHTML = CATS.map((c) => `<button type="button" data-cat="${esc(c)}" class="${draft.targets.includes(`c:${c}`) ? "on" : ""}" title="${esc(CAT_ABOUT[c] || catName(c))}">${keyDot(c)}${esc(catName(c))}</button>`).join("");
  $("#lim-things").innerHTML = draft.targets.filter((t) => t.startsWith("n:")).map((t) =>
    `<li><span class="tag-label">${esc(draft.labels[t] || t.slice(2))}</span><button type="button" class="tag-x" data-t="${esc(t)}" aria-label="Remove ${esc(draft.labels[t] || t.slice(2))}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17"/></svg></button></li>`).join("");
}
$("#lim-cats").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-cat]");
  if (!b) return;
  const t = `c:${b.dataset.cat}`;
  draft.targets = draft.targets.includes(t) ? draft.targets.filter((x) => x !== t) : [...draft.targets, t];
  drawTargets();
});
$("#lim-things").addEventListener("click", (e) => {
  const b = e.target.closest(".tag-x");
  if (b) { draft.targets = draft.targets.filter((x) => x !== b.dataset.t); drawTargets(); }
});
$("#lim-add").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("input", e.target), v = input.value.trim();
  if (!v) return;
  const hit = (sc.range ? sc.range.census : []).find((x) => x.key && x.label.toLowerCase() === v.toLowerCase());
  const k = hit ? hit.key : `n:${v.toLowerCase()}`;
  if (!draft.targets.includes(k)) draft.targets.push(k);
  draft.labels[k] = hit ? hit.label : v;
  input.value = "";
  drawTargets();
});

function drawRules() {
  const host = $("#lim-rules");
  host.innerHTML = "";
  draft.rules.forEach((r, i) => host.append(ruleRow(r, i)));
  $("#lim-add-rule").innerHTML = Object.entries(RULE_KINDS).map(([k, v]) => `<button type="button" data-kind="${k}">+ ${v}</button>`).join("");
}
$("#lim-add-rule").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-kind]");
  if (b) { draft.rules.push(newRule(b.dataset.kind)); drawRules(); }
});

function ruleRow(r, i) {
  const row = document.createElement("div");
  row.className = "rule";
  const num = (k, min, max, label) => `<input type="number" data-k="${k}" min="${min}" max="${max}" value="${r[k] ?? ""}" inputmode="numeric" aria-label="${label}">`;
  const time = (k, label) => `<input type="time" data-k="${k}" value="${r[k]}" step="60" aria-label="${label}">`;
  const allDay = r.kind === "block" && r.from === r.to;
  let f = "";
  if (r.kind === "day") f = `<span>Up to</span>${num("minutes", 0, 1440, "Minutes")}<span>min a day</span>`;
  if (r.kind === "every") f = `<span>Up to</span>${num("minutes", 0, 1440, "Minutes")}<span>min every</span>${num("hours", 1, 24, "Hours")}<span>hours</span>`;
  if (r.kind === "block") f = allDay ? `<span>Blocked all day</span>` : `<span>Blocked from</span>${time("from", "From")}<span>to</span>${time("to", "To")}`;
  row.innerHTML = `<div class="rule-line">${f}<button type="button" class="tag-x rule-x" aria-label="Remove rule"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17"/></svg></button></div>`
    + (r.kind === "block" ? `<div class="toggle-row small"><span>All day</span><label class="switch" aria-label="All day"><input type="checkbox" class="all-day" ${allDay ? "checked" : ""}><i></i></label></div>` : "")
    + (r.days ? `<div class="sched-days">${ALL_DAYS.map((d) => `<button type="button" data-d="${d}" class="${r.days.includes(d) ? "on" : ""}" aria-pressed="${r.days.includes(d)}" aria-label="${LDAYS[d]}">${LDAYS[d][0]}</button>`).join("")}</div>` : "");
  row.addEventListener("input", (e) => {
    const k = e.target.dataset.k;
    if (!k) return;
    if (e.target.type === "time") { if (e.target.value) r[k] = e.target.value.slice(0, 5); }
    else r[k] = e.target.value === "" ? null : Number(e.target.value);
  });
  row.addEventListener("change", (e) => {
    if (!e.target.classList.contains("all-day")) return;
    if (e.target.checked) { r.from = r.to = "00:00"; } else { r.from = "22:00"; r.to = "07:00"; }
    drawRules();
  });
  row.addEventListener("click", (e) => {
    if (e.target.closest(".rule-x")) { draft.rules.splice(i, 1); drawRules(); return; }
    const b = e.target.closest("button[data-d]");
    if (!b) return;
    const d = Number(b.dataset.d), on = r.days.includes(d);
    if (on && r.days.length === 1) return;
    r.days = on ? r.days.filter((x) => x !== d) : [...r.days, d].sort();
    b.classList.toggle("on", !on);
    b.setAttribute("aria-pressed", String(!on));
  });
  return row;
}

$("#lim-unlock-on").addEventListener("change", (e) => $("#lim-unlock").classList.toggle("off", !e.target.checked));

function limitBody() {
  const n = (id, min, max) => { const v = parseInt($(id).value, 10); return Number.isNaN(v) ? null : Math.max(min, Math.min(max, v)); };
  return {
    ...draft, name: $("#lim-name").value.trim(),
    unlock: $("#lim-unlock-on").checked ? { hold: n("#lim-hold", 1, 60) ?? 5, wait: n("#lim-uwait", 0, 240) ?? 0, grant: n("#lim-grant", 1, 240) ?? 10, per_day: n("#lim-perday", 1, 50) } : null,
  };
}

function limitFail(e) { const m = $("#lim-msg"); m.textContent = e.message; m.classList.add("error"); }
async function limitDone(r) {
  limitDialog.close();
  await loadLimits();
  if (r && r.applied === false) toast(`Takes effect at ${whenText(r.at)}`);
}

function drawActions() {
  const box = $("#lim-actions");
  box.innerHTML = (editing ? `<button type="button" class="hold-btn danger" id="lim-del" style="--hold:5s"><span class="hold-fill"></span><span class="hold-text">Hold to delete</span></button>` : "")
    + (editing && editing.pending_at ? `<button type="button" class="ghost" id="lim-keep">Cancel change</button>` : "")
    + `<span class="grow"></span><button type="button" class="ghost" id="lim-close">Close</button><button type="button" class="primary" id="lim-save">Save</button>`;
  $("#lim-close").onclick = () => limitDialog.close();
  const commit = async () => { try { await limitDone(await api("PUT", "/api/limits", limitBody())); } catch (e) { limitFail(e); } };
  $("#lim-save").onclick = async () => {
    try {
      if (editing) {
        const dry = await api("PUT", "/api/limits", { ...limitBody(), dry: true });
        if (dry.wait) {
          const b = $("#lim-save");
          b.outerHTML = `<button type="button" class="hold-btn" id="lim-save" style="--hold:5s"><span class="hold-fill"></span><span class="hold-text">Hold to save</span></button>`;
          bindHold($("#lim-save"), 5, commit);
          const m = $("#lim-msg");
          m.classList.remove("error");
          m.textContent = `This loosens the limit, so it takes effect in ${minText(dry.minutes)}`;
          return;
        }
      }
      await commit();
    } catch (e) { limitFail(e); }
  };
  if (editing) {
    bindHold($("#lim-del"), 5, async () => {
      try { await limitDone(await api("DELETE", `/api/limits/${encodeURIComponent(editing.limit.id)}`)); } catch (e) { limitFail(e); }
    });
  }
  const keep = $("#lim-keep");
  if (keep) keep.onclick = async () => { await api("POST", `/api/limits/${encodeURIComponent(editing.limit.id)}/cancel`, {}); limitDone(); };
}
$("#lim-new").onclick = () => openLimit(null);

/* ---- devices (settings) ---- */
const DEV_KINDS = ["desktop", "laptop", "phone", "tablet"];
async function loadDevices() {
  try { sc.devices = await api("GET", "/api/devices"); } catch { sc.devices = null; }
  paintDevices();
}
function paintDevices() {
  const dv = sc.devices;
  const tileEl = $("#viz-devices");
  if (!dv) {
    if (tileEl) { $("#pill-devices").className = "pill off"; $("#pill-devices").textContent = "Off"; tileEl.innerHTML = `<span class="sys-big">None</span>`; $("#sum-devices").textContent = "Screen time needs the server"; }
    $("#devices").innerHTML = `<li class="hint">Screen time needs the server</li>`;
    return;
  }
  const list = dv.devices || [];
  const live = list.filter((d) => d.silent_for < 900).length;
  const stale = list.filter((d) => d.kind === "phone" ? d.silent_for > 2700 : false);
  if (tileEl) {
    $("#pill-devices").className = `pill ${stale.length ? "warn" : live ? "on" : "set"}`;
    $("#pill-devices").textContent = stale.length ? "Check" : live ? "Live" : "Quiet";
    tileEl.innerHTML = `<span class="sys-big">${list.length} <i>${list.length === 1 ? "device" : "devices"}</i></span>`;
    $("#sum-devices").innerHTML = stale.length ? `<b class="warn">${esc(stale[0].name)} hasn't sent anything in ${esc(agoText(stale[0].silent_for))}</b>` : esc(list.map((d) => d.name).join(", ") || "Screen time from your computers and phone");
  }
  $("#devices").innerHTML = list.map((d) => `<li class="device ${d.silent_for < 900 ? "live" : ""}">
    <button type="button" class="dev-name" data-id="${esc(d.id)}" data-name="${esc(d.name)}" aria-label="Rename ${esc(d.name)}">${esc(d.name)}</button>
    <span class="dev-seen">${d.silent_for < 900 ? "Live" : esc(agoText(d.silent_for))}</span>
    <select data-id="${esc(d.id)}" aria-label="Kind of device, ${esc(d.name)}">${DEV_KINDS.map((k) => `<option value="${k}" ${k === d.kind ? "selected" : ""}>${k[0].toUpperCase() + k.slice(1)}</option>`).join("")}</select>
  </li>`).join("") || `<li class="hint">Nothing has sent screen time yet</li>`;
  $("#dev-cmd").textContent = `curl -fsSL https://raw.githubusercontent.com/ieatfood314/ignition/main/install.sh | sh`;
}
const agoText = (s) => s < 3600 ? `${Math.round(s / 60)} min` : s < 2 * 86400 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} days`;
$("#devices").addEventListener("click", async (e) => {
  const b = e.target.closest(".dev-name");
  if (!b) return;
  const name = prompt("Name", b.dataset.name);
  if (name && name.trim() && name.trim() !== b.dataset.name) {
    try { await api("PATCH", `/api/devices/${encodeURIComponent(b.dataset.id)}`, { name: name.trim() }); } catch (err) { toast(err.message); }
    loadDevices();
  }
});
$("#devices").addEventListener("change", async (e) => {
  const s = e.target.closest("select[data-id]");
  if (!s) return;
  try { await api("PATCH", `/api/devices/${encodeURIComponent(s.dataset.id)}`, { kind: s.value }); } catch (err) { toast(err.message); }
  loadDevices();
});

/* ---- hooks into app.js ---- */
window.screenPage = (page) => {
  if (page === "screen") { renderScreen(); loadScreen(); }
  if (page === "limits") { renderLimits(); loadLimits(); }
  if (page === "settings") loadDevices();
};
window.screenRefresh = () => {
  loadToday();
  loadLimits();
  if (state.page === "screen") loadScreen();
  if (state.page === "settings") loadDevices();
};

let scWidth = 0;
window.addEventListener("resize", () => {
  const w = $("#sc-chart").clientWidth;
  if (state.page === "screen" && w && w !== scWidth) { scWidth = w; renderScreen(); }
});
setInterval(() => {
  if (document.hidden) return;
  loadToday();
  if (state.page === "limits") loadLimits();
}, 60000);
setInterval(() => { if (!document.hidden && state.page === "screen" && sc.date === (sc.day || {}).today && sc.view === "day") loadScreen("day"); }, 120000);
loadToday();
loadLimits();
if (state.page !== "ignite") window.screenPage(state.page);
