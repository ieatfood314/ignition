"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// localStorage can be unavailable (e.g. some WebView/file:// setups); fall back to memory.
const ls = (() => {
  try { localStorage.setItem("ignition._probe", "1"); localStorage.removeItem("ignition._probe"); return localStorage; }
  catch { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }; }
})();

// Inside the Android app the API is served by the app itself through a JS bridge.
const NATIVE = window.IgnitionNative || null;
document.documentElement.classList.add(NATIVE ? "in-app" : "in-web");

const state = {
  items: [],
  ignite: { phase: "idle", active: false, enforcers: [] },
  settings: null,
  clockOffset: 0,
  page: "ignite",
  lastTab: "ignite",
  expanded: new Set(),
  pendingDelete: new Map(),
  rendered: new Set(),
  itemsJson: "",
  sort: { need: ls.getItem("ignition.sort.need") || "smart", want: ls.getItem("ignition.sort.want") || "smart" },
};

// When each reply was asked for and arrived, by the local clock, for working out the server's clock.
const timing = new WeakMap();
const stamp = (data, sent) => {
  if (data && typeof data === "object") timing.set(data, [sent, Date.now() / 1000]);
  return data;
};

async function api(method, path, body) {
  const sent = Date.now() / 1000;
  if (NATIVE) {
    const res = JSON.parse(NATIVE.request(method, path, body ? JSON.stringify(body) : ""));
    if (res.status >= 400) throw new Error((res.body && res.body.error) || `Error ${res.status}`);
    return stamp(res.body, sent);
  }
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return stamp(data, sent);
}

$$("path.flame").forEach((p) => p.setAttribute("d", window.FLAME_PATH || ""));

/* ---------------- formatting ---------------- */

const DAY = 86400000;
const startOfToday = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };
const isoDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const parseDate = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };

function fmtEstimate(min) {
  if (min == null) return "";
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

function fmtDuration(sec) {
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

function dueInfo(due) {
  const diff = Math.round((parseDate(due) - startOfToday()) / DAY);
  let text;
  if (diff < -1) text = `${-diff}d overdue`;
  else if (diff === -1) text = "Yesterday";
  else if (diff === 0) text = "Today";
  else if (diff === 1) text = "Tomorrow";
  else if (diff < 7) text = parseDate(due).toLocaleDateString(undefined, { weekday: "short" });
  else text = parseDate(due).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return { text, hot: diff <= 0, diff };
}

function fmtAge(createdAt) {
  const days = Math.floor((Date.now() / 1000 - createdAt) / 86400);
  if (days < 1) return "added today";
  if (days < 14) return `${days} day${days === 1 ? "" : "s"} on list`;
  if (days < 60) return `${Math.floor(days / 7)} wk on list`;
  if (days < 365) return `${Math.floor(days / 30)} mo on list`;
  return `${(days / 365).toFixed(1)} yr on list`;
}

function fmtClock(sec) {
  sec = Math.max(0, Math.ceil(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

function fmtWhen(ts) {
  const d = new Date(ts * 1000);
  const days = Math.round((new Date(d).setHours(0, 0, 0, 0) - startOfToday()) / DAY);
  const day = days === 0 ? "" : days === 1 ? "tomorrow " : d.toLocaleDateString(undefined, { weekday: "short" }) + " ";
  return day + fmtTime(ts);
}

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const randomId = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, "0")).join("");

const ICON_CHECK = '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const ICON_PLUS = '<svg viewBox="0 0 24 24"><path d="M12 6v12M6 12h12"/></svg>';
const ICON_NOTE = '<svg viewBox="0 0 24 24"><path d="M5 4h14v16H5zM9 9h6M9 13h6M9 17h3"/></svg>';
const ICON_SORT = '<svg viewBox="0 0 24 24"><path d="M7 4v16M3 16l4 4 4-4M17 20V4M13 8l4-4 4 4"/></svg>';

/* ---------------- toast ---------------- */

let toastTimer;
function toast(text, action, onAction, ms = 4500) {
  const el = $("#toast");
  el.innerHTML = `<span>${esc(text)}</span>${action ? `<button type="button">${esc(action)}</button>` : ""}`;
  el.hidden = false;
  if (action) $("button", el).onclick = () => { el.hidden = true; onAction(); };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

/* ---------------- detail fields (shared by add sheet + items) ---------------- */

function buildFields(container, list, values, onChange) {
  container.replaceChildren($("#tpl-fields").content.cloneNode(true));
  if (list === "want") $('[data-field="due"]', container).remove();
  const v = { ...values };

  const estChips = $$("[data-est]", container);
  const estInput = $(".est-input", container);
  const paintEst = () => {
    estChips.forEach((b) => b.classList.toggle("on", Number(b.dataset.est) === v.estimate_min));
    estInput.value = v.estimate_min != null && !estChips.some((b) => Number(b.dataset.est) === v.estimate_min) ? v.estimate_min : "";
  };
  estChips.forEach((b) => b.addEventListener("click", () => {
    const n = Number(b.dataset.est);
    v.estimate_min = v.estimate_min === n ? null : n;
    paintEst(); onChange({ estimate_min: v.estimate_min });
  }));
  estInput.addEventListener("change", () => {
    v.estimate_min = estInput.value === "" ? null : Math.max(0, parseInt(estInput.value, 10) || 0);
    paintEst(); onChange({ estimate_min: v.estimate_min });
  });
  paintEst();

  const dueField = $('[data-field="due"]', container);
  if (dueField) {
    const dueChips = $$("[data-due]", dueField);
    const dueInput = $(".due-input", dueField);
    const chipDate = (b) => isoDate(new Date(startOfToday().getTime() + Number(b.dataset.due) * DAY));
    const paintDue = () => {
      dueChips.forEach((b) => b.classList.toggle("on", v.due === chipDate(b)));
      dueInput.value = v.due || "";
    };
    dueChips.forEach((b) => b.addEventListener("click", () => {
      const d = chipDate(b);
      v.due = v.due === d ? null : d;
      paintDue(); onChange({ due: v.due });
    }));
    dueInput.addEventListener("change", () => {
      v.due = dueInput.value || null;
      paintDue(); onChange({ due: v.due });
    });
    paintDue();
  }

  const priBtns = $$("[data-pri]", container);
  const paintPri = () => priBtns.forEach((b) => b.classList.toggle("on", Number(b.dataset.pri) === (v.priority || 0)));
  priBtns.forEach((b) => b.addEventListener("click", () => {
    v.priority = Number(b.dataset.pri);
    paintPri(); onChange({ priority: v.priority });
  }));
  paintPri();

  const notes = $("textarea", container);
  notes.value = v.notes || "";
  let notesTimer;
  notes.addEventListener("input", () => {
    v.notes = notes.value;
    clearTimeout(notesTimer);
    notesTimer = setTimeout(() => onChange({ notes: v.notes }, true), 500);
  });
  notes.addEventListener("blur", () => { clearTimeout(notesTimer); onChange({ notes: v.notes }, true); });

  return () => v;
}

/* ---------------- sorting ---------------- */

const SORTS = {
  smart: "Smart",
  due: "Due date",
  time: "Quickest first",
  priority: "Priority",
  oldest: "Oldest first",
  newest: "Newest first",
  alpha: "A–Z",
};

const nullsLast = (a, b, cmp) => (a == null) - (b == null) || (a != null && b != null ? cmp(a, b) : 0);
const byDue = (a, b) => nullsLast(a.due, b.due, (x, y) => x.localeCompare(y));
const byTime = (a, b) => nullsLast(a.estimate_min, b.estimate_min, (x, y) => x - y);
const byPri = (a, b) => b.priority - a.priority;
const byOld = (a, b) => a.created_at - b.created_at;

function sortItems(list, items, mode = state.sort[list]) {
  const cmp = {
    smart: list === "need" ? [byDue, byPri, byOld] : [byPri, byOld],
    due: [byDue, byPri, byOld],
    time: [byTime, byPri, byOld],
    priority: list === "need" ? [byPri, byDue, byOld] : [byPri, byOld],
    oldest: [byOld],
    newest: [(a, b) => b.created_at - a.created_at],
    alpha: [(a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" })],
  }[mode] || [byOld];
  return items.sort((a, b) => { for (const c of cmp) { const r = c(a, b); if (r) return r; } return 0; });
}

function paintSortButtons() {
  $$(".sort-btn").forEach((b) => {
    const mode = state.sort[b.dataset.sortFor];
    b.innerHTML = `${ICON_SORT}<span>${SORTS[mode]}</span>`;
    b.classList.toggle("on", mode !== "smart");
  });
}

function openSortMenu(btn) {
  const list = btn.dataset.sortFor;
  const menu = $("#sort-menu");
  menu.innerHTML = Object.entries(SORTS)
    .filter(([k]) => !(list === "want" && k === "due"))
    .map(([k, label]) => `<button type="button" role="menuitemradio" aria-checked="${state.sort[list] === k}" data-mode="${k}">${label}</button>`)
    .join("");
  $$("button", menu).forEach((b) => b.onclick = () => {
    state.sort[list] = b.dataset.mode;
    ls.setItem(`ignition.sort.${list}`, b.dataset.mode);
    closeSortMenu();
    paintSortButtons();
    renderLists(true);
  });
  const r = btn.getBoundingClientRect();
  menu.hidden = false;
  menu.style.top = `${r.bottom + window.scrollY + 6}px`;
  menu.style.left = `${Math.max(12, Math.min(r.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 12))}px`;
}
const closeSortMenu = () => { $("#sort-menu").hidden = true; };
$$(".sort-btn").forEach((b) => b.addEventListener("click", (e) => {
  e.stopPropagation();
  $("#sort-menu").hidden ? openSortMenu(b) : closeSortMenu();
}));
document.addEventListener("click", (e) => { if (!e.target.closest("#sort-menu")) closeSortMenu(); });

/* ---------------- lists ---------------- */

function metaHtml(it) {
  const bits = [];
  if (it.priority) bits.push(`<span class="pri p${it.priority}" title="priority"><i></i><i></i><i></i></span>`);
  if (it.estimate_min != null) bits.push(`<span>${fmtEstimate(it.estimate_min)}</span>`);
  if (it.list === "need" && it.due) {
    const d = dueInfo(it.due);
    bits.push(`<span class="${d.hot && !it.done_at ? "hot" : ""}">${esc(d.text)}</span>`);
  }
  if (it.list === "want") bits.push(`<span>${fmtAge(it.created_at)}</span>`);
  if (it.notes && it.notes.trim()) bits.push(`<span class="has-notes" title="has notes">${ICON_NOTE}</span>`);
  return bits.join("");
}

function itemEl(it) {
  const li = document.createElement("li");
  li.className = "item" + (it.done_at ? " is-done" : "") + (state.rendered.has(it.id) ? "" : " fresh");
  li.dataset.id = it.id;
  li.dataset.pri = it.priority;
  const open = state.expanded.has(it.id);
  li.innerHTML = `
    <div class="item-row">
      <button class="check" type="button" aria-label="${it.done_at ? "Mark not done" : "Mark done"}">${ICON_CHECK}</button>
      <div class="item-main">
        <div class="title">${esc(it.title)}</div>
        <div class="meta">${metaHtml(it)}</div>
      </div>
      <button class="mini-plus" type="button" aria-label="Notes and details" aria-expanded="${open}">${ICON_PLUS}</button>
    </div>`;
  $(".check", li).onclick = () => toggleDone(it);
  const toggle = () => { open ? state.expanded.delete(it.id) : state.expanded.add(it.id); renderLists(true); };
  $(".mini-plus", li).onclick = toggle;
  $(".item-main", li).onclick = toggle;
  if (open) li.append(extraEl(it));
  return li;
}

function extraEl(it) {
  const wrap = document.createElement("div");
  wrap.className = "item-extra";
  const title = document.createElement("input");
  title.className = "edit-title";
  title.value = it.title;
  title.maxLength = 500;
  title.addEventListener("change", () => {
    const t = title.value.trim();
    if (!t) { title.value = it.title; return; }
    patchItem(it.id, { title: t });
  });
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") title.blur(); });
  const fields = document.createElement("div");
  fields.className = "fields";
  buildFields(fields, it.list, it, (patch, quiet) => patchItem(it.id, patch, quiet));
  const other = it.list === "need" ? "want" : "need";
  const actions = document.createElement("div");
  actions.className = "item-actions";
  actions.innerHTML = `<button type="button" data-a="move">Move to ${other === "need" ? "Need" : "Want"}</button><button type="button" class="danger" data-a="del">Delete</button>`;
  $('[data-a="move"]', actions).onclick = () => {
    state.expanded.delete(it.id);
    patchItem(it.id, { list: other });
    toast(`Moved to ${other === "need" ? "Need to do" : "Want to do"}`);
  };
  $('[data-a="del"]', actions).onclick = () => deleteItem(it);
  wrap.append(title, fields, actions);
  return wrap;
}

function renderLists(force) {
  const active = document.activeElement;
  if (!force && active && active.closest(".item-extra")) return;
  for (const list of ["need", "want"]) {
    const all = state.items.filter((i) => i.list === list && !state.pendingDelete.has(i.id));
    const open = sortItems(list, all.filter((i) => !i.done_at));
    const done = all.filter((i) => i.done_at).sort((a, b) => b.done_at - a.done_at);
    $(`#items-${list}`).replaceChildren(...open.map(itemEl));
    $(`#count-${list}`).textContent = open.length || "";
    $(`#empty-${list}`).hidden = open.length > 0;
    const dg = $(`#done-${list}`);
    dg.hidden = done.length === 0;
    $("summary", dg).textContent = `Done · ${done.length}`;
    $("ul", dg).replaceChildren(...done.slice(0, 100).map(itemEl));
  }
  state.rendered = new Set(state.items.map((i) => i.id));
  renderUpNext();
}

function renderUpNext() {
  const open = state.items.filter((i) => i.list === "need" && !i.done_at && !state.pendingDelete.has(i.id));
  const next = sortItems("need", open, "smart").slice(0, 3);
  const box = $("#up-next");
  box.hidden = next.length === 0;
  $("ul", box).innerHTML = next.map((it) => {
    const right = [it.estimate_min != null ? fmtEstimate(it.estimate_min) : "", it.due ? dueInfo(it.due).text : ""].filter(Boolean).join(" · ");
    return `<li><span>${esc(it.title)}</span><span>${esc(right)}</span></li>`;
  }).join("");
}

function upsert(item) {
  const i = state.items.findIndex((x) => x.id === item.id);
  if (i >= 0) state.items[i] = item; else state.items.push(item);
}

async function loadItems() {
  try {
    const items = await api("GET", "/api/items");
    const json = JSON.stringify(items);
    if (json === state.itemsJson) return;
    state.itemsJson = json;
    state.items = items;
    renderLists();
  } catch (e) { console.error(e); }
}

async function patchItem(id, patch, quiet) {
  const it = state.items.find((x) => x.id === id);
  if (it) {
    Object.assign(it, patch);
    if ("done" in patch) it.done_at = patch.done ? Date.now() / 1000 : null;
  }
  if (!quiet) renderLists(true);
  try {
    upsert(await api("PATCH", `/api/items/${id}`, patch));
    if (!quiet) renderLists(true);
  } catch (e) {
    toast(`Couldn't save: ${e.message}`);
    loadItems();
  }
}

function toggleDone(it) {
  const nowDone = !it.done_at;
  const row = $(`.item[data-id="${it.id}"]`);
  if (row && nowDone) { row.classList.add("is-done", "burning-off"); FX.haptic("tick"); }
  state.expanded.delete(it.id);
  setTimeout(() => patchItem(it.id, { done: nowDone }), nowDone && !FX.reduced ? 620 : 0);
  if (nowDone) toast("Done.", "Undo", () => patchItem(it.id, { done: false }));
}

function deleteItem(it) {
  state.expanded.delete(it.id);
  const timer = setTimeout(async () => {
    state.pendingDelete.delete(it.id);
    try { await api("DELETE", `/api/items/${it.id}`); } catch (e) { toast(`Couldn't delete: ${e.message}`); }
    state.items = state.items.filter((x) => x.id !== it.id);
  }, 5000);
  state.pendingDelete.set(it.id, timer);
  renderLists(true);
  toast("Deleted.", "Undo", () => {
    clearTimeout(state.pendingDelete.get(it.id));
    state.pendingDelete.delete(it.id);
    renderLists(true);
  }, 5000);
}

/* ---------------- add sheet ---------------- */

const addDialog = $("#add-dialog");
let addList = "need";
let getAddValues = () => ({});

function openAdd() {
  addList = state.page === "want" ? "want" : "need";
  $("#add-title").placeholder = addList === "want" ? "What do you want to do?" : "What needs doing?";
  $("#add-title").value = "";
  $("#add-fields").hidden = true;
  $("#add-more").setAttribute("aria-expanded", "false");
  getAddValues = buildFields($("#add-fields"), addList, { priority: 0 }, () => {});
  addDialog.showModal();
  $("#add-title").focus();
}

$("#fab").onclick = openAdd;
$("#add-cancel").onclick = () => addDialog.close();
addDialog.addEventListener("click", (e) => { if (e.target === addDialog) addDialog.close(); });
$("#add-more").onclick = () => {
  const f = $("#add-fields");
  f.hidden = !f.hidden;
  $("#add-more").setAttribute("aria-expanded", String(!f.hidden));
};
$("#add-title").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("#add-form").requestSubmit(); }
});
$("#add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const title = $("#add-title").value.trim();
  if (!title) { $("#add-title").focus(); return; }
  const v = getAddValues();
  const body = { list: addList, title, estimate_min: v.estimate_min ?? null, priority: v.priority || 0, notes: v.notes || "" };
  if (addList === "need") body.due = v.due || null;
  addDialog.close();
  try {
    upsert(await api("POST", "/api/items", body));
    renderLists(true);
  } catch (err) {
    toast(`Couldn't add: ${err.message}`);
  }
});

/* ---------------- ignite ---------------- */

const R = 176, RING = 2 * Math.PI * R, CX = 200;
const serverNow = () => Date.now() / 1000 + state.clockOffset;

// The server says what time it is in whole seconds, so one reply only pins its clock to within a second:
// it was at least `now` when the reply arrived, and under `now + 1` when the request went out. Each reply
// narrows that window and the clock runs from the middle of it, so the countdown never lurches a second
// every time it checks in. If the window turns inside out, a clock was changed: start again.
const offsetWindow = { lo: -Infinity, hi: Infinity };
function syncClock(s) {
  const [sent, recv] = timing.get(s) || [Date.now() / 1000, Date.now() / 1000];
  const lo = s.now - recv, hi = s.now + 1 - sent;
  const w = offsetWindow;
  if (lo > w.hi + 0.5 || hi < w.lo - 0.5) { w.lo = -Infinity; w.hi = Infinity; }
  w.lo = Math.max(w.lo, lo);
  w.hi = Math.min(w.hi, hi);
  if (w.lo > w.hi) { w.lo = lo; w.hi = hi; }
  state.clockOffset = (w.lo + w.hi) / 2;
}
let arming = null;
let lastPhase = null;

// A new version of the page was deployed: reload to pick it up, once nothing is half-done.
function reloadIfStale(build) {
  if (!build) return; // the Android app updates as a whole
  state.build = state.build || build;
  if (build === state.build) return;
  const busy = arming || mainDial.dragging() || subDial.dragging() || addDialog.open || saveTimer || mecoDialog.open ||
    ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName);
  if (!busy) location.reload();
}

function setIgnite(s, quiet) {
  reloadIfStale(s.build);
  const was = state.ignite;
  syncClock(s);
  state.ignite = s;
  if (!quiet && was.session && was.phase === "countdown" && s.phase === "burning" && s.session && s.session.id === was.session.id) {
    liftoff();
  }
  if (!quiet && was.phase === "burning" && s.phase === "idle" && !state.stopping &&
      serverNow() >= was.session.ends_at - 2 && serverNow() - was.session.ends_at < 15 * 60) {
    showCutoff(was.session);
    FX.haptic("reject");
  }
  state.stopping = false;
  renderIgnite();
  if (state.page === "settings") paintSummaries(); // the goal gauges and the board follow the burns
  if (state.page === "settings" && editBlock && !!was.block_locked !== !!s.block_locked) {
    renderBlock();
    if (settingsPage === "apps" && installedApps) renderAppPicker();
  }
}

async function loadIgnite() {
  try { setIgnite(await api("GET", "/api/ignite")); } catch (e) { console.error(e); }
}

async function igniteAction(path, body, okToast) {
  try {
    setIgnite(await api("POST", path, body), true);
    if (okToast) toast(okToast);
  } catch (e) { toast(e.message); }
}

/** Liftoff: the warp, the flash, the console kicking under you. */
function liftoff(text = "Liftoff.") {
  FX.liftoff();
  FX.replay($("#dial"), "kick");
  toast(text);
}

/* ---- the dial: the bezel sets the burn length like a minute hand; each lap round adds an hour */

const dialEl = $("#dial"), bezel = $("#bezel");
const launch = {
  minutes: Math.min(720, Math.max(1, parseInt(ls.getItem("ignition.minutes"), 10) || 50)),
  delay: Math.min(1440, Math.max(0, parseInt(ls.getItem("ignition.delay"), 10) || 0)),
};

const polar = (frac, r) => {
  const a = frac * 2 * Math.PI - Math.PI / 2;
  return [CX + Math.cos(a) * r, CX + Math.sin(a) * r];
};

$("#ticks").innerHTML = Array.from({ length: 60 }, (_, i) => {
  const major = i % 5 === 0;
  const [x1, y1] = polar(i / 60, major ? 184 : 188);
  const [x2, y2] = polar(i / 60, 197);
  return `<line class="${major ? "major" : ""}" style="--i:${i}" x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}"/>`;
}).join("");
const ticks = $$("#ticks line");

function paintTicks(frac, cls = "lit") {
  const n = Math.round(frac * 60);
  ticks.forEach((t, i) => t.classList.toggle(cls, i < n));
}

/** The arc from twelve o'clock round to `frac` of the ring, and the bright head at its end. */
function paintArc(frac) {
  frac = Math.max(0, Math.min(1, frac));
  const fill = $("#ring-fill");
  fill.style.strokeDasharray = `${RING}`;
  fill.style.strokeDashoffset = `${RING * (1 - frac)}`;
  const [x, y] = polar(frac, R);
  const halo = $(".halo", bezel);
  halo.style.strokeDasharray = `${RING}`;
  halo.style.strokeDashoffset = `${RING * (1 - frac)}`;
  const head = $("#head");
  head.setAttribute("cx", x.toFixed(2));
  head.setAttribute("cy", y.toFixed(2));
  return [x, y];
}

function paintSetting() {
  const m = launch.minutes;
  const lap = m % 60 === 0 ? 1 : (m % 60) / 60;
  const [x, y] = paintArc(lap);
  $("#knob").setAttribute("transform", `translate(${x.toFixed(2)} ${y.toFixed(2)})`);
  paintTicks(lap);
  // One pip per full hour, in a short arc under twelve o'clock.
  const hours = Math.floor((m - 1) / 60);
  $("#pips").innerHTML = Array.from({ length: hours }, (_, i) => {
    const [px, py] = polar((i - (hours - 1) / 2) * 0.022, 158);
    return `<circle cx="${px.toFixed(2)}" cy="${py.toFixed(2)}" r="3"/>`;
  }).join("");
  bezel.setAttribute("aria-valuenow", m);
  bezel.setAttribute("aria-valuetext", fmtEstimate(m));
}

function setMinutes(m, tick = true) {
  m = Math.max(1, Math.min(720, Math.round(m)));
  if (m === launch.minutes) return;
  launch.minutes = m;
  ls.setItem("ignition.minutes", String(m));
  if (tick) FX.haptic("tick");
  renderIgnite();
}

/**
 * Makes an SVG dial turnable like a minute hand: each lap is an hour, and going round past twelve adds (or
 * takes off) one. Grab it anywhere on the ring and turn, tap the ring to jump there, scroll, or use the
 * arrow keys. A mouse grabbing the ring moves the hand there first; a finger turns it from where it is.
 * The SVG has touch-action: none (in CSS), since Chrome ignores touch-action on shapes inside an SVG and
 * would take the drag as a scroll. `centre` is what tapping the middle does, if anything.
 */
function turnable(svg, { cx, r, inner, get, set, min, max, step, centre = null }) {
  let drag = null;
  const snap = (m) => Math.max(min, Math.min(max, Math.round(m / step) * step));
  const point = (e) => {
    const b = svg.getBoundingClientRect(), k = (2 * cx) / b.width;
    const x = (e.clientX - b.left) * k - cx, y = (e.clientY - b.top) * k - cx;
    let a = Math.atan2(y, x) + Math.PI / 2;
    if (a < 0) a += 2 * Math.PI;
    return { frac: a / (2 * Math.PI), dist: Math.hypot(x, y) / r };
  };
  // A jump to a spot on the ring: that minute past whichever hour is nearest to where the dial is now.
  const at = (frac) => {
    const prev = get();
    let best = frac * 60;
    for (let h = 1; h < 12; h++) if (Math.abs(h * 60 + frac * 60 - prev) < Math.abs(best - prev)) best = h * 60 + frac * 60;
    return snap(best);
  };
  const live = () => state.ignite.phase === "idle" && !arming;

  svg.addEventListener("pointerdown", (e) => {
    if (!live()) return;
    const p = point(e);
    if (p.dist < inner) {
      if (centre) drag = { id: e.pointerId, tap: [e.clientX, e.clientY], centre: true };
      return;
    }
    const onKnob = !!e.target.closest(".knob");
    e.preventDefault();
    svg.setPointerCapture(e.pointerId);
    drag = { id: e.pointerId, frac: p.frac, value: get(), start: [e.clientX, e.clientY], moved: onKnob, jump: !onKnob && e.pointerType === "mouse" };
    svg.closest(".console, .subdial").classList.add("dragging");
  });
  svg.addEventListener("pointermove", (e) => {
    if (!drag || drag.id !== e.pointerId || drag.tap) return;
    if (!drag.moved) {
      if (Math.hypot(e.clientX - drag.start[0], e.clientY - drag.start[1]) < 6) return;
      drag.moved = true;
      if (drag.jump) { drag.value = at(drag.frac); set(drag.value); }
    }
    const { frac } = point(e);
    let d = frac - drag.frac;
    d -= Math.round(d); // the short way round
    drag.frac = frac;
    drag.value = Math.max(0, Math.min(max, drag.value + d * 60));
    set(snap(drag.value));
  });
  const end = (e) => {
    if (!drag || drag.id !== e.pointerId) return;
    const tapped = e.type === "pointerup" && (drag.tap ? Math.hypot(e.clientX - drag.tap[0], e.clientY - drag.tap[1]) < 12 : !drag.moved);
    if (tapped) drag.centre ? centre() : set(at(drag.frac));
    drag = null;
    svg.closest(".console, .subdial").classList.remove("dragging");
  };
  ["pointerup", "pointercancel"].forEach((ev) => svg.addEventListener(ev, end));
  let wheel = 0;
  svg.addEventListener("wheel", (e) => {
    if (!live()) return;
    e.preventDefault();
    wheel += e.deltaMode ? e.deltaY * 40 : e.deltaY;
    while (Math.abs(wheel) >= 40) {
      const up = wheel < 0, m = get();
      wheel -= Math.sign(wheel) * 40;
      set(up ? Math.floor(m / step) * step + step : Math.ceil(m / step) * step - step);
    }
  }, { passive: false });
  svg.addEventListener("keydown", (e) => {
    if (!live()) return;
    const d = { ArrowUp: step, ArrowRight: step, ArrowDown: -step, ArrowLeft: -step, PageUp: 60, PageDown: -60 }[e.key];
    if (d) { e.preventDefault(); set(get() + (e.shiftKey ? Math.sign(d) : d)); }
    if (e.key === "Enter") $("#ignite-btn").click();
  });
  return { dragging: () => !!drag };
}

const mainDial = turnable(bezel, {
  cx: CX, r: R, inner: 0.72, min: 1, max: 720, step: 1,
  get: () => launch.minutes, set: (m) => setMinutes(m),
});

/* ---- the T-minus dial: a small one under the big one, in white, the same way round */

const sub = $("#tminus-dial");
const SUB_R = 74, SUB_C = 100, SUB_RING = 2 * Math.PI * SUB_R;
$("#sub-ticks").innerHTML = Array.from({ length: 12 }, (_, i) => {
  const a = (i / 12) * 2 * Math.PI - Math.PI / 2, r1 = i % 3 ? 86 : 83;
  const p = (r) => [(SUB_C + Math.cos(a) * r).toFixed(2), (SUB_C + Math.sin(a) * r).toFixed(2)];
  const [x1, y1] = p(r1), [x2, y2] = p(93);
  return `<line class="${i % 3 ? "" : "major"}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
}).join("");

function setDelay(d) {
  d = Math.max(0, Math.min(1440, Math.round(d)));
  if (d === launch.delay) return;
  launch.delay = d;
  ls.setItem("ignition.delay", String(d));
  FX.haptic("tick");
  renderIgnite();
}

const subDial = turnable(sub, {
  cx: SUB_C, r: SUB_R, inner: 0.55, min: 0, max: 1440, step: 5,
  get: () => launch.delay, set: setDelay, centre: () => setDelay(0),
});

/** T-minus as the dial shows it: "15", or "1:15" from an hour up. */
const fmtDelay = (d) => (d >= 60 ? `${Math.floor(d / 60)}:${String(d % 60).padStart(2, "0")}` : String(d));

function paintDelay() {
  const d = launch.delay;
  const lap = d === 0 ? 0 : d % 60 === 0 ? 1 : (d % 60) / 60;
  const arc = $("#sub-arc");
  arc.style.strokeDasharray = `${SUB_RING}`;
  arc.style.strokeDashoffset = `${SUB_RING * (1 - lap)}`;
  const a = lap * 2 * Math.PI - Math.PI / 2;
  $("#sub-knob").setAttribute("transform", `translate(${(SUB_C + Math.cos(a) * SUB_R).toFixed(2)} ${(SUB_C + Math.sin(a) * SUB_R).toFixed(2)})`);
  $$("#sub-ticks line").forEach((t, i) => t.classList.toggle("lit", d > 0 && i < Math.round(lap * 12)));
  const val = $("#tminus-val");
  const text = fmtDelay(d);
  if (val.textContent !== text) val.textContent = text;
  $("#tminus-unit").textContent = d >= 60 ? "h:min" : "min";
  $(".subdial").classList.toggle("zero", d === 0);
  sub.setAttribute("aria-valuenow", d);
  sub.setAttribute("aria-valuetext", d ? `T-minus ${fmtEstimate(d)}` : "No T-minus");
}

/* ---- painting the console */

function setReadout(text, decode) {
  const el = $("#ignite-label");
  if (el.dataset.text === text) return false;
  el.dataset.text = text;
  el.dataset.len = text.length > 7 ? "xl" : text.length > 5 ? "l" : text.length > 2 ? "m" : "s";
  if (decode) FX.decode(el, text); else el.textContent = text;
  return true;
}

function renderIgnite() {
  const s = state.ignite;
  const sess = s.session;
  const t = serverNow();
  const phase = s.phase || (s.active ? "burning" : "idle");
  const look = arming ? "arming" : phase;
  const changed = look !== lastPhase;
  dialEl.dataset.phase = look;
  document.documentElement.dataset.phase = look;
  $("#launch-params").hidden = phase !== "idle";
  $("#session-info").hidden = phase === "idle";
  bezel.tabIndex = phase === "idle" ? 0 : -1;
  let tabLabel = "Ignite", barFrac = null, emit = null;

  if (phase === "burning") {
    const remaining = sess.ends_at - t;
    const total = sess.ends_at - sess.starts_at;
    setReadout(fmtClock(remaining), changed);
    $("#ignite-sub").textContent = "Burning";
    const frac = remaining / total;
    emit = paintArc(frac);
    paintTicks(frac);
    $("#until").textContent = `Locked until ${fmtTime(sess.ends_at)}`;
    $("#extend").hidden = false;
    $("#stop-btn").hidden = !s.can_stop;
    $("#locked-note").hidden = !!s.can_stop;
    $("#locked-note").textContent = "No way out. Get to work.";
    $("#scrub-btn").hidden = true;
    tabLabel = fmtClock(remaining);
    barFrac = 1 - frac;
  } else if (phase === "countdown") {
    const left = sess.starts_at - t;
    const total = Math.max(1, sess.starts_at - sess.created_at);
    setReadout(`T−${fmtClock(left)}`, changed);
    $("#ignite-sub").textContent = "Ignite now";
    const frac = Math.min(1, left / total);
    paintArc(frac);
    paintTicks(frac);
    $("#until").textContent = `Ignition ${fmtTime(sess.starts_at)} · ${fmtEstimate(sess.minutes)} burn`;
    $("#extend").hidden = true;
    $("#stop-btn").hidden = true;
    // Under an hour to liftoff it's committed, bar a minute's grace right after setting it.
    const scrubLeft = (s.scrub_until ?? Infinity) - t;
    $("#locked-note").hidden = scrubLeft > 0;
    $("#locked-note").textContent = "Committed. No scrubbing now.";
    $("#scrub-btn").hidden = scrubLeft <= 0;
    $("#scrub-btn").textContent = scrubLeft <= 3600 ? `Scrub launch · ${fmtClock(scrubLeft)}` : "Scrub launch";
    tabLabel = `T−${fmtClock(left)}`;
  } else if (arming) {
    if (setReadout(String(arming.count))) FX.replay($("#ignite-label"), "slam");
    $("#ignite-sub").textContent = "Tap to scrub";
    paintArc(1);
  } else {
    setReadout(fmtClock(launch.minutes * 60), changed && lastPhase !== null);
    $("#ignite-sub").textContent = launch.delay ? `Ignite · T−${fmtDelay(launch.delay)}` : "Ignite";
    paintSetting();
    paintDelay();
    $("#abort-rule").textContent = launch.minutes > 60 ? "Hold to end early" : "Locked in. No ending early.";
    $("#abort-rule").classList.toggle("locked", launch.minutes <= 60);
  }
  lastPhase = look;

  FX.mode(look);
  emitAt = emit;
  aimEmbers();

  const bar = $("#burnbar");
  bar.hidden = barFrac == null;
  if (barFrac != null) $("div", bar).style.width = `${100 * barFrac}%`;
  const tab = $('.tabs a[data-tab="ignite"]');
  tab.classList.toggle("burning", phase !== "idle");
  const tabLbl = $("#tab-ignite-label");
  if (tabLbl.textContent !== tabLabel) { tabLbl.textContent = tabLabel; moveTabInd(); }

  renderBurnStats(s.stats);
  renderNextSchedule(s.next_schedule, phase);
  renderWindowNote(phase);

  $("#enforcers").innerHTML = (s.enforcers || []).map((e) => {
    const stale = e.age > 30;
    const armed = !stale && e.status && e.status.armed;
    const label = stale ? "offline" : armed ? "locked" : phase === "burning" ? "arming" : "ready";
    const err = !stale && e.status && e.status.error ? ` title="${esc(e.status.error)}"` : "";
    return `<li class="${stale ? "stale" : armed ? "armed" : "ready"}"${err}><b>${esc(e.host)}</b><span>${label}${err ? " ⚠" : ""}</span></li>`;
  }).join("");
}

/** Embers fly off the head of the ring while it burns down, outward and away. */
let emitAt = null;
function aimEmbers() {
  if (!emitAt || state.page !== "ignite") { FX.emitter(null); return; }
  const r = bezel.getBoundingClientRect();
  const k = r.width / 400;
  FX.emitter({ x: r.left + emitAt[0] * k, y: r.top + emitAt[1] * k, dx: (emitAt[0] - CX) / R, dy: (emitAt[1] - CX) / R });
}

/** Keep the starfield's vanishing point on the dial, wherever the page has put it. */
function aimSky() {
  if (state.page !== "ignite") { FX.focus(window.innerWidth / 2, window.innerHeight * 0.4); return; }
  const r = bezel.getBoundingClientRect();
  if (r.width) FX.focus(r.left + r.width / 2, r.top + r.height / 2);
}
window.addEventListener("scroll", () => { aimSky(); aimEmbers(); }, { passive: true });
window.addEventListener("resize", () => { aimSky(); aimEmbers(); });

/** Fuel-gauge style: a row of cells, lit up to how far you've got. */
function gauge(frac) {
  const n = 24, lit = Math.round(Math.min(1, frac) * n);
  return `<span class="gauge">${Array.from({ length: n }, (_, i) => `<i class="${i < lit ? "on" : ""}" style="--i:${i}"></i>`).join("")}</span>`;
}

function renderBurnStats(st) {
  const box = $("#burn-stats");
  if (!st) { box.innerHTML = ""; return; }
  const goals = st.goals || {};
  const row = (label, sec, goalMin, unit) => {
    if (!goalMin) return "";
    const frac = sec / 60 / goalMin;
    const val = unit === "h" ? `${(sec / 3600).toFixed(1).replace(/\.0$/, "")}/${(goalMin / 60).toFixed(1).replace(/\.0$/, "")} h` : `${Math.round(sec / 60)}/${goalMin} min`;
    return `<div class="goal${frac >= 1 ? " met" : ""}"><span class="goal-label">${label}</span>${gauge(frac)}<span class="goal-val">${val}</span></div>`;
  };
  const rows = row("Today", st.today, goals.daily, "m") + row("Week", st.week, goals.weekly, "h");
  const html = rows || `<p class="burned">Today ${fmtDuration(st.today)} · week ${fmtDuration(st.week)} · <a class="link" href="#settings/goals">set goals</a></p>`;
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
}

function renderNextSchedule(ns, phase) {
  const el = $("#next-sched");
  if (!ns || phase !== "idle") { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `Next launch ${esc(fmtWhen(ns.at))} · ${fmtEstimate(ns.minutes)} <button type="button" class="link">skip</button>`;
  $("button", el).onclick = () => igniteAction("/api/ignite/skip", { schedule_id: ns.schedule_id, date: ns.date }, "Launch skipped.");
}

/** The launch window (Settings › Launch window), if it's open right now. */
function openWindow() {
  const lw = state.settings && state.settings.launch_window;
  if (!lw || !lw.enabled) return null;
  const d = new Date(serverNow() * 1000);
  if (!lw.days.includes(d.getDay() || 7)) return null;
  const at = (hm) => { const [h, m] = hm.split(":").map(Number); const x = new Date(d); x.setHours(h, m, 0, 0); return x.getTime() / 1000; };
  const t = d.getTime() / 1000;
  return t >= at(lw.start) && t < at(lw.end) ? { ...lw, until: at(lw.end) } : null;
}

function renderWindowNote(phase) {
  const el = $("#window-note");
  const lw = phase === "idle" && !arming ? openWindow() : null;
  el.hidden = !lw;
  if (!lw) return;
  const label = lw.tminus ? `Start T−${lw.tminus}` : "Ignite now";
  const key = `${lw.until}|${label}|${lw.minutes}`;
  if (el.dataset.key === key) return;
  el.dataset.key = key;
  el.innerHTML = `Launch window open till ${esc(fmtTime(lw.until))} <button type="button" class="link">${esc(label)}</button> <span>· ${fmtEstimate(lw.minutes)}</span>`;
  $("button", el).onclick = async () => {
    try {
      setIgnite(await api("POST", "/api/ignite", { minutes: lw.minutes, delay: lw.tminus }), true);
      if (lw.tminus) toast(`Countdown started. T−${lw.tminus}.`); else liftoff();
    } catch (e) { toast(e.message); }
  };
}

async function loadSettings() {
  try {
    const s = await api("GET", "/api/settings");
    state.settings = s;
    renderIgnite();
    if (state.page === "stats") renderStats(); // the goal line
    // Changed on another device while Settings is open: show it, unless you're typing here.
    const typing = document.activeElement?.matches("#page-settings input, #page-settings textarea");
    if (state.page === "settings" && settingsDoc && !saveTimer && !typing && stable(s) !== stable(settingsDoc)) fillSettings(s);
  } catch (e) { console.error(e); }
}

$("#ignite-btn").addEventListener("click", () => {
  const phase = state.ignite.phase;
  if (phase === "burning") return;
  if (phase === "countdown") {
    api("POST", "/api/ignite/now").then((r) => { setIgnite(r, true); liftoff(); }, (e) => toast(e.message));
    return;
  }
  if (arming) {
    clearInterval(arming.timer);
    arming = null;
    FX.haptic("reject");
    toast("Scrubbed.");
    renderIgnite();
    return;
  }
  const { minutes, delay } = launch;
  arming = { count: 3, minutes, delay };
  FX.spool(3);
  arming.timer = setInterval(async () => {
    arming.count -= 1;
    if (arming.count > 0) { FX.spool(arming.count); renderIgnite(); return; }
    clearInterval(arming.timer);
    const { minutes: m, delay: d } = arming;
    arming = null;
    try {
      setIgnite(await api("POST", "/api/ignite", { minutes: m, delay: d }), true);
      if (d) { FX.haptic("confirm"); toast(`Countdown started. T−${d}.`); } else liftoff();
    } catch (e) {
      toast(e.message);
      renderIgnite();
    }
  }, 1000);
  renderIgnite();
});

$$("[data-extend]").forEach((b) => b.addEventListener("click", () => {
  FX.haptic("tick");
  FX.replay($("#dial"), "boost");
  igniteAction("/api/ignite/extend", { minutes: Number(b.dataset.extend) }, `+${b.dataset.extend} min.`);
}));
$("#scrub-btn").addEventListener("click", () => igniteAction("/api/ignite/scrub", null, "Launch scrubbed."));

(() => {
  const btn = $("#stop-btn");
  let t;
  const cancel = () => { clearTimeout(t); btn.classList.remove("holding"); dialEl.classList.remove("venting"); };
  btn.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    btn.classList.add("holding");
    dialEl.classList.add("venting");
    FX.haptic("heavy");
    t = setTimeout(async () => {
      cancel();
      state.stopping = true;
      try { setIgnite(await api("POST", "/api/ignite/stop"), true); FX.cutoff(); toast("Engines off. Ended early."); }
      catch (err) { state.stopping = false; toast(err.message); }
    }, 2000);
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => btn.addEventListener(ev, cancel));
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
})();

/* ---------------- settings ---------------- */

const settingsEl = $("#page-settings");
const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
let appInfo = null;
let settingsDoc = null;   // the shared settings document as last loaded or saved
let editBlock = null;     // the block lists being edited
let installedApps = null;
let settingsPage = "home";

const fmtHm = (hm) => { const [h, m] = hm.split(":").map(Number); return fmtTime(new Date(2000, 0, 1, h, m).getTime() / 1000); };
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function dayButtons(container, days) {
  container.innerHTML = DAY_LETTERS.map((l, i) =>
    `<button type="button" class="${days.includes(i + 1) ? "on" : ""}" data-day="${i + 1}" aria-label="day ${i + 1}">${l}</button>`).join("");
  $$("button", container).forEach((b) => b.onclick = () => { b.classList.toggle("on"); edited(); });
}
const readDays = (container) => $$("button.on", container).map((b) => Number(b.dataset.day));

function fmtDays(days) {
  const k = [...days].sort().join("");
  if (k === "1234567") return "Every day";
  if (k === "12345") return "Weekdays";
  if (k === "67") return "Weekends";
  return days.map((d) => ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d - 1]).join(", ");
}

function scheduleEl(sc) {
  const li = $("#tpl-schedule").content.firstElementChild.cloneNode(true);
  li.dataset.id = sc.id;
  $(".sched-time", li).value = sc.time;
  $(".sched-min", li).value = sc.minutes;
  $(".sched-on", li).checked = sc.enabled !== false;
  $(".sched-lead-min", li).value = sc.lead ?? 5;
  dayButtons($(".sched-days", li), sc.days);
  $(".sched-del", li).onclick = () => { li.remove(); edited(); };
  return li;
}

function readSchedules() {
  return $$("#schedules .schedule").map((li) => ({
    id: li.dataset.id,
    time: $(".sched-time", li).value || "09:00",
    days: readDays($(".sched-days", li)),
    minutes: parseInt($(".sched-min", li).value, 10) || 50,
    lead: parseInt($(".sched-lead-min", li).value || "0", 10) || 0,
    enabled: $(".sched-on", li).checked,
  }));
}

/* ---- launch window */

function fillWindow(lw) {
  $("#lw-on").checked = lw.enabled;
  dayButtons($("#lw-days"), lw.days);
  $("#lw-start").value = lw.start;
  $("#lw-end").value = lw.end;
  $("#lw-after").value = lw.after;
  $("#lw-minutes").value = lw.minutes;
  $("#lw-tminus").value = lw.tminus;
  setTrigger(lw.trigger);
}

function setTrigger(trigger) {
  $$("#lw-trigger button").forEach((b) => b.classList.toggle("on", b.dataset.trigger === trigger));
  $("#lw-home").hidden = trigger !== "home";
  paintHome();
}

function readWindow() {
  const num = (id, d) => { const n = parseInt($(id).value, 10); return Number.isFinite(n) ? n : d; };
  return {
    enabled: $("#lw-on").checked,
    days: readDays($("#lw-days")),
    start: $("#lw-start").value || "15:00",
    end: $("#lw-end").value || "17:30",
    trigger: ($("#lw-trigger button.on") || { dataset: { trigger: "home" } }).dataset.trigger,
    after: num("#lw-after", 20),
    minutes: num("#lw-minutes", 50),
    tminus: num("#lw-tminus", 10),
  };
}

function paintHome() {
  const el = $("#lw-home-text");
  if (!NATIVE) { el.textContent = "Your phone works out when you're home, from its Wi-Fi. Pick the network in the Android app."; return; }
  const h = appInfo && appInfo.launch_window;
  if (!h || !h.home) { el.textContent = "Home Wi-Fi isn't set yet. Tap below while you're on it."; return; }
  el.textContent = `Home Wi-Fi: ${h.home}${h.here ? " (connected)" : ""}`;
}

$$("#lw-trigger button").forEach((b) => b.onclick = () => { setTrigger(b.dataset.trigger); edited(); });
$("#lw-on").onchange = () => { $("#lw-body").classList.toggle("off", !$("#lw-on").checked); edited(); };
$("#lw-home-set").onclick = async () => {
  try { appInfo = await api("PUT", "/native/app", { home_wifi: "current" }); paintHome(); }
  catch (e) { toast(e.message); }
};

/* ---- blocking */

function tagEl(label, title, onRemove) {
  const li = $("#tpl-tag").content.firstElementChild.cloneNode(true);
  $(".tag-label", li).textContent = label;
  if (title) li.title = title;
  if (onRemove) $(".tag-x", li).onclick = onRemove;
  else $(".tag-x", li).remove();
  return li;
}

// During a burn (or a countdown past scrubbing) the block list only grows; the server enforces it too.
// Things added since the last save can still come off.
const blockLocked = () => !!state.ignite.block_locked && !!settingsDoc;
const lockedBlock = () => (blockLocked() ? settingsDoc.block : { sites: [], programs: [], apps: [], games: false });

let seenTags = null;
function renderBlock() {
  const b = editBlock;
  const held = lockedBlock();
  const rm = (on, fn) => (on ? null : fn);
  $("#block-lock").hidden = !blockLocked();
  const keys = new Set([...b.sites.map((x) => `s:${x}`), ...b.programs.map((x) => `p:${x}`), ...b.apps.map((x) => `a:${x.id}`)]);
  const fresh = seenTags ? [...keys].filter((k) => !seenTags.has(k)) : [];
  seenTags = keys;
  $("#block-sites").replaceChildren(...b.sites.map((s) => tagEl(s, null, rm(held.sites.includes(s), () => { b.sites = b.sites.filter((x) => x !== s); blockEdited(); }))));
  $("#block-programs").replaceChildren(...b.programs.map((p) => tagEl(p, null, rm(held.programs.includes(p), () => { b.programs = b.programs.filter((x) => x !== p); blockEdited(); }))));
  const apps = [...b.apps].sort((x, y) => x.label.localeCompare(y.label));
  $("#block-apps").replaceChildren(...apps.map((a) => tagEl(a.label, a.id, rm(held.apps.some((x) => x.id === a.id), () => { b.apps = b.apps.filter((x) => x.id !== a.id); blockEdited(); }))));
  $("#block-games").checked = b.games;
  $("#block-games").disabled = held.games;
  const flash = (sel, pre, list) => $$(`${sel} li`).forEach((li, i) => { if (fresh.includes(pre + list[i])) li.classList.add("new"); });
  flash("#block-sites", "s:", b.sites);
  flash("#block-programs", "p:", b.programs);
  flash("#block-apps", "a:", apps.map((x) => x.id));
  $("#cnt-sites").textContent = b.sites.length || "";
  $("#cnt-programs").textContent = b.programs.length || "";
  $("#cnt-apps").textContent = [b.apps.length || "", b.games ? "games" : ""].filter(Boolean).join(" + ");
  paintSummaries();
}
const blockEdited = () => { renderBlock(); scheduleSave(); };

function cleanSite(v) {
  let s = v.trim().toLowerCase().replace(/^[a-z]+:\/\//, "").split(/[/?#]/)[0].split("@").pop().replace(/^\.+|\.+$/g, "");
  if (s.startsWith("www.")) s = s.slice(4);
  return /^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d{1,5})?$/.test(s) ? s : null;
}

function tagForm(form, add) {
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("input", form);
    const bits = input.value.split(/[\s,]+/).filter(Boolean);
    const bad = bits.filter((v) => !add(v));
    input.value = bad.join(" ");
    if (bad.length) toast(`Not sure what "${bad[0]}" is.`);
    blockEdited();
  });
}
tagForm($("#block-sites-add"), (v) => { const s = cleanSite(v); if (s && !editBlock.sites.includes(s)) editBlock.sites.push(s); return !!s; });
tagForm($("#block-programs-add"), (v) => {
  if (!/^[A-Za-z0-9._+-]{1,64}$/.test(v)) return false;
  if (!editBlock.programs.includes(v)) editBlock.programs.push(v);
  return true;
});
tagForm($("#block-apps-add"), (v) => {
  if (!/^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$/.test(v)) return false;
  if (!editBlock.apps.some((a) => a.id === v)) editBlock.apps.push({ id: v, label: v });
  return true;
});
$("#block-games").onchange = (e) => { editBlock.games = e.target.checked; edited(); };

async function openAppPicker() {
  if (!installedApps) {
    $("#app-pick").innerHTML = `<li class="hint">Loading…</li>`;
    await new Promise((r) => setTimeout(r, 30)); // let it paint before the blocking native call
    try { installedApps = await api("GET", "/native/apps"); } catch (e) { $("#app-pick").innerHTML = `<li class="hint">${esc(e.message)}</li>`; return; }
  }
  $("#app-search").value = "";
  renderAppPicker();
}

function renderAppPicker() {
  if (!editBlock) return;
  const q = $("#app-search").value.trim().toLowerCase();
  const chosen = new Set(editBlock.apps.map((a) => a.id));
  const held = new Set(lockedBlock().apps.map((a) => a.id));
  const list = installedApps.filter((a) => !q || a.label.toLowerCase().includes(q) || a.id.includes(q));
  $("#app-pick").innerHTML = list.map((a) => `
    <li><label><input type="checkbox" data-id="${esc(a.id)}" ${chosen.has(a.id) ? "checked" : ""} ${held.has(a.id) ? "disabled" : ""}>
      <span>${esc(a.label)}${a.game ? ' <em class="game">game</em>' : ""}<small>${esc(a.id)}</small></span></label></li>`).join("");
  $$("#app-pick input").forEach((cb) => cb.onchange = () => {
    const app = installedApps.find((a) => a.id === cb.dataset.id);
    editBlock.apps = editBlock.apps.filter((a) => a.id !== app.id);
    if (cb.checked) editBlock.apps.push({ id: app.id, label: app.label });
    blockEdited();
  });
}
$("#app-search").addEventListener("input", renderAppPicker);
$("#block-apps-pick").onclick = () => { location.hash = "settings/apps"; };

/* ---- pages */

// Settings drills down: the board of systems, then one system. Going in slides in from the right, coming
// back slides the board in from the left.
const SET_DEPTH = { home: 0, apps: 2 };

function goSettings(page) {
  if (!$(`.set-page[data-page="${page}"]`, settingsEl) || (page === "apps" && !NATIVE)) page = "home";
  const was = settingsPage;
  settingsPage = page;
  settingsEl.classList.toggle("at-home", page === "home");
  $$(".set-page", settingsEl).forEach((p) => { p.hidden = p.dataset.page !== page; });
  const sub = page !== "home";
  const title = sub ? $(`.set-page[data-page="${page}"]`, settingsEl).dataset.title : "Settings";
  const h = $("#settings-title");
  if (h.textContent !== title) { h.textContent = title; FX.decode(h, title, 300); }
  $("#settings-back").hidden = !sub;
  $("#settings-back").href = page === "apps" ? "#settings/blocking" : "#settings";
  if (was !== page) {
    const deeper = (SET_DEPTH[page] ?? 1) > (SET_DEPTH[was] ?? 1);
    const el = sub ? $(`.set-page[data-page="${page}"]`, settingsEl) : $(".set-home", settingsEl);
    el.classList.remove("push", "pop");
    FX.replay(el, deeper ? "push" : "pop");
    clearTimeout(el.slideTimer);
    el.slideTimer = setTimeout(() => el.classList.remove("push", "pop"), 900);
  }
  window.scrollTo(0, 0);
  paintSummaries();
  if (page === "apps") openAppPicker();
}

const settingsSub = () => location.hash.split("/")[1] || "home";

function settingsBack() {
  if (settingsPage === "apps") location.hash = "settings/blocking";
  else if (settingsPage !== "home") location.hash = "settings";
  else location.hash = state.lastTab;
}

function readSettingsForm() {
  return {
    goals: {
      daily: parseInt($("#goal-daily").value || "0", 10) || 0,
      weekly: Math.round((parseFloat($("#goal-weekly").value || "0") || 0) * 60),
    },
    schedules: readSchedules(),
    block: editBlock,
    launch_window: readWindow(),
  };
}

const lamp = (k, st) => { const el = $(`#lamp-${k}`); if (el) el.className = `lamp ${st}`; };

/** The next time a scheduled launch fires, from the launches as they stand in the form. */
function nextLaunch(schedules) {
  const now = new Date();
  for (let d = 0; d < 8; d++) {
    const day = addDays(startOfToday(), d);
    const wd = day.getDay() || 7;
    const hits = schedules.filter((x) => x.enabled && x.days.includes(wd)).map((x) => {
      const [h, m] = x.time.split(":").map(Number);
      return { at: new Date(day.getFullYear(), day.getMonth(), day.getDate(), h, m), x };
    }).filter((h) => h.at > now).sort((p, q) => p.at - q.at);
    if (hits.length) return { ...hits[0], d };
  }
  return null;
}

/** "40 s ago", "5 min ago", "3 h ago", or a time and day for anything older. */
function fmtAgo(ts) {
  const d = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (d < 10) return "just now";
  if (d < 60) return `${d} s ago`;
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 6 * 3600) return `${Math.floor(d / 3600)} h ago`;
  return fmtWhen(ts);
}

/** A tile's badge: a word, and a light that says the same thing. */
const pill = (k, word, st) => { const el = $(`#pill-${k}`); if (el) { el.className = `pill ${st}`; el.textContent = word; } };
const tile = (k, big, small, extra = "") => {
  $(`#viz-${k}`).innerHTML = `<span class="sys-big">${big}</span>${extra}`;
  $(`#sum-${k}`).innerHTML = small;
};

/** The board: each tile says, in words, what that part is doing right now. */
function paintSummaries() {
  if (!settingsDoc) return;
  const f = readSettingsForm();
  const st = state.ignite.stats;
  const hrs = (sec) => `${+(sec / 3600).toFixed(1)}`;

  const { daily, weekly } = f.goals;
  if (daily || weekly) {
    const met = st && daily && st.today / 60 >= daily;
    pill("goals", met ? "Met today" : "On", met ? "on" : "set");
    const today = st && daily ? `${fmtDuration(st.today)} <i>of ${fmtEstimate(daily)} today</i>` : `${fmtEstimate(daily || 0)} <i>a day</i>`;
    const week = weekly ? `This week ${st ? `${hrs(st.week)} of ` : ""}${+(weekly / 60).toFixed(1)} h` : "No weekly goal";
    tile("goals", daily ? today : `${st ? hrs(st.week) : 0} h <i>of ${+(weekly / 60).toFixed(1)} h this week</i>`,
      daily ? esc(week) : "No daily goal", st && daily ? gauge(st.today / 60 / daily) : "");
  } else {
    pill("goals", "Off", "off");
    tile("goals", "None set", "How much you want to burn a day or week");
  }

  const on = f.schedules.filter((x) => x.enabled).length;
  const nl = nextLaunch(f.schedules);
  pill("launches", on ? "On" : "Off", on ? "set" : "off");
  if (nl) {
    const day = nl.d === 0 ? "today" : nl.d === 1 ? "tomorrow" : nl.at.toLocaleDateString(undefined, { weekday: "long" });
    tile("launches", `${esc(fmtTime(nl.at / 1000))} <i>${esc(day)}</i>`, `Next one · ${fmtEstimate(nl.x.minutes)} burn${f.schedules.length > 1 ? ` · ${f.schedules.length} set up` : ""}`);
  } else {
    tile("launches", "None", f.schedules.length ? `${plural(f.schedules.length, "launch")}, all switched off` : "Starts burns by itself at set times");
  }

  const lw = f.launch_window;
  if (lw.enabled) {
    const open = openWindow();
    pill("window", open ? "Open now" : "On", open ? "on" : "set");
    const hmDate = (hm) => { const [h, m] = hm.split(":").map(Number); return new Date(2000, 0, 1, h, m); };
    tile("window", `${esc(timeRange.formatRange(hmDate(lw.start), hmDate(lw.end)))} <i>${esc(fmtDays(lw.days).toLowerCase())}</i>`,
      `Nudges you ${lw.after} min after ${lw.trigger === "home" ? "you get home" : "it opens"}`);
  } else {
    pill("window", "Off", "off");
    tile("window", "Off", "Nudges you to start a burn when you get home");
  }

  const b = f.block;
  const parts = [b.sites.length && plural(b.sites.length, "site"), b.apps.length && plural(b.apps.length, "app"),
    b.programs.length && plural(b.programs.length, "program")].filter(Boolean);
  const count = b.sites.length + b.apps.length + b.programs.length;
  const phoneOff = appInfo && !appInfo.blocker.enabled;
  if (count || b.games) {
    pill("blocking", phoneOff ? "Fix" : "On", phoneOff ? "warn" : "set");
    tile("blocking", count ? `${count} blocked${b.games ? " <i>+ every game</i>" : ""}` : "Every game",
      phoneOff ? `<b class="warn">Not on this phone: blocker is off</b>` : esc(parts.join(" · ") || "Blocked during every burn"));
  } else {
    pill("blocking", "Off", "off");
    tile("blocking", "Nothing yet", "Pick the sites, apps and programs to block");
  }

  if (appInfo) {
    const bl = appInfo.blocker.enabled;
    lamp("blocker", bl ? "on" : "warn");
    const missing = [!appInfo.notifications.granted && "notifications"].filter(Boolean);
    pill("phone", bl && !missing.length ? "OK" : "Fix", bl && !missing.length ? "on" : "warn");
    const extras = [appInfo.ring.enabled && "rings at liftoff", appInfo.dnd.enabled && "Do Not Disturb"].filter(Boolean);
    tile("phone", bl ? (missing.length ? "Notifications off" : "All set") : "Blocker is off",
      bl ? esc(extras.join(" · ") || "Blocker on") : `<b class="warn">Apps and sites won't be blocked here</b>`);

    const link = syncState();
    const s = appInfo.sync || {};
    pill("sync", { up: "Working", down: "Failing", off: "Off" }[link], { up: "on", down: "warn", off: "off" }[link]);
    const host = appInfo.server_url ? appInfo.server_url.replace(/^https?:\/\//, "").split(/[:/]/)[0] : "";
    if (link === "up") tile("sync", `Synced <i>${esc(fmtAgo(s.last_ok))}</i>`, esc(`with ${host}`));
    else if (link === "down") tile("sync", s.last_ok ? `Last worked <i>${esc(fmtAgo(s.last_ok))}</i>` : "Never synced", `<b class="warn">${esc(s.last_error || "Can't reach the server")}</b>`);
    else tile("sync", "Off", "Everything stays on this phone");
  }
  goalPainters.forEach((p) => p());
  renderPlans();
}

/* ---- the flight plan: a week of launches (and the launch window) at a glance */

function renderPlans() {
  if (state.page !== "settings" || !settingsDoc) return;
  if (settingsPage === "launches") renderPlan($("#plan-launches"), "launches");
  if (settingsPage === "window") renderPlan($("#plan-window"), "window");
}

function renderPlan(el, focus) {
  const W = el.clientWidth;
  if (!W) return;
  const f = readSettingsForm();
  const left = 18, rowH = 16, gap = 5, H = 7 * (rowH + gap) + 16;
  const x = (min) => left + ((W - left) * Math.max(0, Math.min(1440, min))) / 1440;
  const y = (d) => (d - 1) * (rowH + gap);
  const hm = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
  let out = `<defs><pattern id="hatch-${focus}" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="2" height="5"/></pattern></defs>`;
  for (let d = 1; d <= 7; d++) {
    out += `<text x="0" y="${y(d) + rowH / 2 + 3.5}">${DAY_LETTERS[d - 1]}</text><rect class="lane" x="${left}" y="${y(d)}" width="${W - left}" height="${rowH}" rx="2"/>`;
  }
  for (const h of [6, 12, 18]) out += `<line class="hr" x1="${x(h * 60)}" x2="${x(h * 60)}" y1="0" y2="${7 * (rowH + gap) - gap}"/>`;
  for (const h of [0, 6, 12, 18]) out += `<text x="${x(h * 60) + (h ? 0 : 0)}" y="${H - 3}" text-anchor="${h ? "middle" : "start"}">${esc(fmtHour(h))}</text>`;

  const lw = f.launch_window;
  if (lw.enabled) {
    for (const d of lw.days) {
      const a = hm(lw.start), b = hm(lw.end);
      out += `<rect class="win" fill="url(#hatch-${focus})" x="${x(a)}" y="${y(d)}" width="${Math.max(2, x(b) - x(a))}" height="${rowH}"/>`;
      if (lw.trigger === "time" && focus === "window") out += `<rect class="nudge" x="${x(a + lw.after) - 1}" y="${y(d) - 2}" width="2" height="${rowH + 4}"/>`;
    }
  }
  let k = 0;
  for (const sc of f.schedules) {
    const a = hm(sc.time), cls = sc.enabled ? "blk" : "blk off";
    for (const d of sc.days) {
      const pieces = [[d, a, a + sc.minutes]];
      if (a + sc.minutes > 1440) pieces[0][2] = 1440, pieces.push([(d % 7) + 1, 0, a + sc.minutes - 1440]);
      if (sc.lead && sc.enabled) out += `<rect class="lead" x="${x(a - sc.lead)}" y="${y(d) + rowH / 2 - 1}" width="${Math.max(1, x(a) - x(a - sc.lead))}" height="2"/>`;
      for (const [dd, p, q] of pieces) {
        out += `<rect class="${cls}" style="--k:${k++}" x="${x(p)}" y="${y(dd)}" width="${Math.max(3, x(q) - x(p))}" height="${rowH}" rx="2"/>`;
      }
    }
  }
  const now = new Date(), nd = now.getDay() || 7, nm = now.getHours() * 60 + now.getMinutes();
  out += `<line class="now" x1="${x(nm)}" x2="${x(nm)}" y1="${y(nd) - 3}" y2="${y(nd) + rowH + 3}"/><circle class="now" cx="${x(nm)}" cy="${y(nd) - 3}" r="2.5"/>`;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="This week's launches">${out}</svg>`;
}
window.addEventListener("resize", () => renderPlans());

/* ---- burn goals: fuel gauges you drag, with the number above them still there to type into */

function goalGauge(ctl) {
  const step = Number(ctl.dataset.step), cells = Number(ctl.dataset.cells);
  const weekly = ctl.id === "gc-weekly";
  const input = $("input", ctl), g = $(".gc-gauge", ctl);
  g.innerHTML = Array.from({ length: cells }, (_, i) => `<i style="--i:${i}"></i>`).join("") + `<b class="gc-mark"></b>`;
  const cellEls = $$("i", g);
  const minutes = () => (weekly ? Math.round((parseFloat(input.value) || 0) * 60) : parseInt(input.value, 10) || 0);
  const paint = () => {
    const m = minutes(), n = Math.min(cells, Math.round(m / step));
    cellEls.forEach((c, i) => c.classList.toggle("on", i < n));
    ctl.classList.toggle("off", !m);
    g.setAttribute("aria-valuenow", m);
    g.setAttribute("aria-valuetext", m ? fmtEstimate(m) : "Off");
    const st = state.ignite.stats;
    const done = st ? (weekly ? st.week : st.today) : 0;
    $(".gc-mark", g).style.left = `${Math.min(100, (done / 60 / (step * cells)) * 100)}%`;
    $(".gc-mark", g).hidden = !st;
    $(".gc-now", ctl).textContent = st ? `${weekly ? "This week" : "Today"} ${fmtDuration(done)}` : "";
  };
  const set = (m) => {
    if (m === minutes()) return;
    input.value = m ? (weekly ? m / 60 : m) : "";
    FX.haptic("tick");
    paint();
    edited();
  };
  const at = (e) => {
    const r = g.getBoundingClientRect(), fr = (e.clientX - r.left) / r.width;
    return fr < 0.5 / cells ? 0 : Math.min(cells, Math.ceil(fr * cells)) * step;
  };
  let drag = false;
  g.addEventListener("pointerdown", (e) => { e.preventDefault(); g.setPointerCapture(e.pointerId); drag = true; g.classList.add("dragging"); set(at(e)); });
  g.addEventListener("pointermove", (e) => { if (drag) set(at(e)); });
  ["pointerup", "pointercancel"].forEach((ev) => g.addEventListener(ev, () => { drag = false; g.classList.remove("dragging"); }));
  g.addEventListener("keydown", (e) => {
    const d = { ArrowRight: step, ArrowUp: step, ArrowLeft: -step, ArrowDown: -step }[e.key];
    if (d) { e.preventDefault(); set(Math.max(0, Math.min(cells * step, Math.round(minutes() / step) * step + d))); }
  });
  input.addEventListener("input", paint);
  return paint;
}
const goalPainters = $$(".goal-ctl").map(goalGauge);

/** up: synced and the last try worked; down: the last try failed; off: not syncing. */
function syncState() {
  if (!appInfo || !appInfo.server_url) return "off";
  const s = appInfo.sync;
  if (s && s.last_error && (!s.last_ok || s.last_error_at > s.last_ok)) return "down";
  return s && s.last_ok ? "up" : "down";
}
settingsEl.addEventListener("input", (e) => { if (!e.target.closest("#app-search, #server-url")) edited(); });

function fillSettings(s) {
  settingsDoc = s;
  state.settings = s;
  $("#goal-daily").value = s.goals.daily || "";
  $("#goal-weekly").value = s.goals.weekly ? +(s.goals.weekly / 60).toFixed(1) : "";
  $("#schedules").replaceChildren(...s.schedules.map(scheduleEl));
  editBlock = JSON.parse(JSON.stringify(s.block));
  goalPainters.forEach((p) => p());
  fillWindow(s.launch_window);
  $("#lw-body").classList.toggle("off", !s.launch_window.enabled);
  renderBlock();
  if (settingsPage === "apps" && installedApps) renderAppPicker();
}

async function openSettings() {
  try { fillSettings(await api("GET", "/api/settings")); } catch (e) { toast(e.message); }
  if (NATIVE) await loadAppInfo();
}

async function loadAppInfo() {
  try {
    appInfo = await api("GET", "/native/app");
    $("#server-url").value = appInfo.server_url || "";
    paintSyncStatus();
    $("#dnd-toggle").checked = !!appInfo.dnd.enabled;
    $("#dnd-hint").textContent = appInfo.dnd.enabled && !appInfo.dnd.granted ? "Needs Do Not Disturb access. Tap the toggle again to grant it." : "";
    $("#notif-btn").textContent = appInfo.notifications.granted ? "Allowed" : "Allow";
    $("#notif-btn").disabled = !!appInfo.notifications.granted;
    const bl = appInfo.blocker;
    $("#blocker-btn").textContent = bl.enabled ? "On" : "Turn on";
    $("#blocker-btn").disabled = !!bl.enabled;
    $("#blocker-state").textContent = bl.enabled
      ? "watching for blocked apps and sites during burns"
      : "needs accessibility access to see which app or site is open";
    $("#blocker-hint").hidden = !!bl.enabled;
    $("#blocker-hint").textContent = "The blocker is off on this phone, so apps and sites won't be blocked here. Turn it on in Settings › Phone.";
    $("#ring-toggle").checked = !!appInfo.ring.enabled;
    $("#ring-sound-row").hidden = !appInfo.ring.enabled;
    $$("#ring-sound [data-sound]").forEach((b) => b.classList.toggle("on", b.dataset.sound === appInfo.ring.sound));
    $("#ring-hint").textContent = appInfo.ring.enabled && !appInfo.ring.full_screen ? "Full-screen alerts are off for Ignition, so it can only ring, not light up the lock screen." : "";
    paintHome();
    paintSummaries();
  } catch (e) { $("#sync-status").textContent = e.message; }
}

/** The Sync page: a verdict in plain words, then the facts behind it. */
function paintSyncStatus() {
  const link = syncState();
  const s = (appInfo && appInfo.sync) || {};
  const host = appInfo && appInfo.server_url ? appInfo.server_url.replace(/^https?:\/\//, "").split(/[:/]/)[0] : "";
  $("#link-viz").className = `link-viz ${link}`;
  $("#link-host").textContent = host || "Server";
  const head = $("#sync-head");
  head.className = `sync-head ${link}`;
  head.innerHTML = {
    up: `<b>Sync is working</b>`,
    down: `<b>Sync isn't working</b>`,
    off: `<b>Sync is off</b>`,
  }[link];
  const rows = [];
  if (link === "off") {
    rows.push(["Now", "Lists, burns and settings stay on this phone"]);
  } else {
    rows.push(["Last synced", s.last_ok ? `${fmtTime(s.last_ok)} · ${fmtAgo(s.last_ok)}` : "Never"]);
    if (link === "down") rows.push(["Problem", `<b class="warn">${esc(s.last_error || "Can't reach the server")}</b>${s.last_error_at ? ` · ${esc(fmtAgo(s.last_error_at))}` : ""}`]);
    rows.push(["How often", "Every 10 s while the app is open, about every 15 min when it isn't"]);
    const comps = (state.ignite.enforcers || []).slice().sort((p, q) => p.age - q.age);
    rows.push(["Computers", comps.length
      ? comps.map((e) => `<span class="comp${e.age > 30 ? " stale" : ""}"><i></i>${esc(e.host)} <em>${e.age > 30 ? `last seen ${esc(fmtAgo(Date.now() / 1000 - e.age))}` : "online"}</em></span>`).join("")
      : "None have checked in yet"]);
  }
  $("#sync-facts").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v.startsWith("<") ? v : esc(v)}</dd>`).join("");
}

/* ---- saving: every edit saves itself a moment later, and anything pending goes when you leave */

// JSON with sorted keys, so two copies of the settings compare equal whatever order their keys are in.
const stable = (v) => JSON.stringify(v, (k, x) => x && typeof x === "object" && !Array.isArray(x)
  ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => (a < b ? -1 : 1))) : x);

const settingsChanged = () => settingsDoc && stable(readSettingsForm()) !== stable({
  goals: settingsDoc.goals, schedules: settingsDoc.schedules, block: settingsDoc.block, launch_window: settingsDoc.launch_window,
});

let saveTimer = null;
function scheduleSave() {
  if (!settingsDoc) return;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveSettings, 600);
}
function edited() { paintSummaries(); scheduleSave(); }

async function saveSettings() {
  clearTimeout(saveTimer);
  saveTimer = null;
  if (!settingsChanged()) return;
  try {
    settingsDoc = await api("PUT", "/api/settings", readSettingsForm());
    state.settings = settingsDoc;
    if (stable(settingsDoc.block) !== stable(editBlock)) { editBlock = JSON.parse(JSON.stringify(settingsDoc.block)); renderBlock(); }
    loadIgnite();
  } catch (e) { toast(`Couldn't save: ${e.message}`); }
}
const flushSettings = () => { if (saveTimer) saveSettings(); };

$("#add-schedule").onclick = () => {
  $("#schedules").append(scheduleEl({ id: randomId(), time: "09:00", minutes: 50, lead: 5, enabled: true, days: [1, 2, 3, 4, 5] }));
  edited();
};
$("#server-test").onclick = async () => {
  $("#sync-status").textContent = "Checking…";
  await new Promise((r) => setTimeout(r, 50)); // let "Checking…" paint before the blocking native call
  try {
    appInfo = await api("POST", "/native/app/server", { server_url: $("#server-url").value.trim() });
    $("#sync-status").textContent = appInfo.server_url ? (syncState() === "up" ? "Connected. Saved." : "Saved, but it didn't connect.") : "Sync turned off.";
    paintSyncStatus();
    loadItems(); loadIgnite();
  } catch (e) { $("#sync-status").textContent = e.message; }
};
$("#dnd-toggle").onchange = async (e) => {
  try { appInfo = await api("PUT", "/native/app", { dnd_enabled: e.target.checked }); } catch (err) { toast(err.message); }
  loadAppInfo();
};
$("#ring-toggle").onchange = async (e) => {
  try { appInfo = await api("PUT", "/native/app", { ring_enabled: e.target.checked }); } catch (err) { toast(err.message); }
  loadAppInfo();
};
$$("#ring-sound [data-sound]").forEach((b) => b.onclick = async () => {
  try { appInfo = await api("PUT", "/native/app", { ring_sound: b.dataset.sound }); } catch (err) { toast(err.message); }
  loadAppInfo();
});
$("#ring-test").onclick = async () => { try { await api("POST", "/native/app/ring-test"); } catch (e) { toast(e.message); } };
$("#blocker-btn").onclick = async () => { try { appInfo = await api("POST", "/native/app/blocker"); } catch (e) { toast(e.message); } loadAppInfo(); };
$("#notif-btn").onclick = async () => { try { await api("POST", "/native/app/notifications"); } catch (e) { toast(e.message); } };

/* ---------------- main engine cutoff ---------------- */

const mecoDialog = $("#meco-dialog");
let secondStage = 25;

function paintSecond() {
  $$("#meco-chips [data-second]").forEach((b) => b.classList.toggle("on", Number(b.dataset.second) === secondStage));
  $("#meco-min").value = $$("#meco-chips [data-second]").some((b) => Number(b.dataset.second) === secondStage) ? "" : secondStage;
}

function showCutoff(sess) {
  const mins = sess.minutes;
  $("#meco-title").textContent = `${plural(mins, "minute")} burned.`;
  secondStage = sess.planned || mins;
  paintSecond();
  FX.cutoff();
  if (!mecoDialog.open) mecoDialog.showModal();
}

$$("#meco-chips [data-second]").forEach((b) => b.onclick = () => { secondStage = Number(b.dataset.second); paintSecond(); });
$("#meco-min").addEventListener("change", () => { const n = parseInt($("#meco-min").value, 10); if (n >= 1 && n <= 720) { secondStage = n; paintSecond(); } });
$("#meco-done").onclick = () => mecoDialog.close();
mecoDialog.addEventListener("click", (e) => { if (e.target === mecoDialog) mecoDialog.close(); });
$("#meco-go").onclick = async () => {
  mecoDialog.close();
  try {
    setIgnite(await api("POST", "/api/ignite", { minutes: secondStage }), true);
    liftoff("Second stage ignition.");
  } catch (e) { toast(e.message); }
};

/* ---------------- stats ---------------- */

// The server hands over raw burns; they're bucketed here, so days and hours are in this device's timezone.
let burnsData = null;
let statsKey = "";
let logShown = 12;
state.range = ["week", "month", "year", "all"].includes(ls.getItem("ignition.range")) ? ls.getItem("ignition.range") : "week";

async function loadBurns() {
  try {
    burnsData = await api("GET", "/api/burns");
    if (state.page === "stats") renderStats();
  } catch (e) { console.error(e); }
}

const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const dayOf = (sec) => { const d = new Date(sec * 1000); d.setHours(0, 0, 0, 0); return d; };
const secOf = (d) => d.getTime() / 1000;
const mondayOf = (d) => addDays(d, -((d.getDay() + 6) % 7));
// "6 PM", or "18:00" where clocks run to 24 (a bare "18" reads like a count).
const fmtHour = (h) => new Date(2000, 0, 1, h).toLocaleTimeString(undefined,
  new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions().hour12 ? { hour: "numeric" } : { hour: "2-digit", minute: "2-digit" });
const weekdayName = (i, weekday = "short") => new Date(2024, 0, 1 + i).toLocaleDateString(undefined, { weekday }); // 2024-01-01 was a Monday
const dateFmt = (d, opts) => d.toLocaleDateString(undefined, { ...opts, ...(d.getFullYear() !== new Date().getFullYear() && { year: "numeric" }) });
const timeRange = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

function fmtTotal(sec) {
  return sec >= 100 * 3600 ? `${Math.round(sec / 3600)}h` : fmtDuration(sec);
}

function dayLabel(d) {
  const diff = Math.round((startOfToday() - d) / DAY);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return dateFmt(d, { weekday: "short", month: "short", day: "numeric" });
}

function crunch(range, burns, now) {
  const today = dayOf(now);
  const first = burns.length ? dayOf(burns[0].start) : today;
  let from, unit;
  if (range === "week") { from = addDays(today, -6); unit = "day"; }
  else if (range === "month") { from = addDays(today, -29); unit = "day"; }
  else if (range === "year") { from = new Date(today.getFullYear(), today.getMonth() - 11, 1); unit = "month"; }
  else {
    const span = Math.round((today - first) / DAY) + 1;
    unit = span <= 31 ? "day" : span <= 26 * 7 ? "week" : "month";
    from = unit === "day" ? (span < 7 ? addDays(today, -6) : first) : unit === "week" ? mondayOf(first) : new Date(first.getFullYear(), first.getMonth(), 1);
  }

  const a = secOf(from);
  const inRange = burns.map((x) => ({ ...x, s: Math.max(x.start, a), e: Math.min(x.end, now) })).filter((x) => x.e > x.s);
  const burned = (p, q) => inRange.reduce((sum, x) => sum + Math.max(0, Math.min(x.e, q) - Math.max(x.s, p)), 0);

  const buckets = [];
  for (let d = from; d <= today;) {
    const next = unit === "day" ? addDays(d, 1) : unit === "week" ? addDays(d, 7) : new Date(d.getFullYear(), d.getMonth() + 1, 1);
    let label, tip;
    if (unit === "day") {
      label = range === "week" ? weekdayName((d.getDay() + 6) % 7) : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      tip = dayLabel(d);
    } else if (unit === "week") {
      label = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      tip = `Week of ${dateFmt(d, { month: "short", day: "numeric" })}`;
    } else {
      label = d.getMonth() ? d.toLocaleDateString(undefined, { month: "short" }) : String(d.getFullYear());
      tip = d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    }
    buckets.push({ sec: burned(secOf(d), secOf(next)), label, tip });
    d = next;
  }

  // Burned time per weekday (Monday first) and hour, split at each hour boundary.
  const grid = Array.from({ length: 7 }, () => new Array(24).fill(0));
  for (const x of inRange) {
    for (let t = x.s; t < x.e;) {
      const at = new Date(t * 1000);
      const next = new Date(at);
      next.setMinutes(60, 0, 0);
      const end = Math.min(x.e, secOf(next));
      grid[(at.getDay() + 6) % 7][at.getHours()] += end - t;
      t = end;
    }
  }

  // Days in a row with any burn, up to today (or yesterday, if today hasn't had one yet).
  const burnedDays = new Set();
  for (const x of burns) for (let d = dayOf(x.start); secOf(d) < x.end; d = addDays(d, 1)) burnedDays.add(isoDate(d));
  let streak = 0;
  for (let d = burnedDays.has(isoDate(today)) ? today : addDays(today, -1); burnedDays.has(isoDate(d)); d = addDays(d, -1)) streak++;

  const total = burned(a, now + 1);
  const days = Math.round((today - (first > from ? first : from)) / DAY) + 1;
  const span = range === "week" ? "in the last 7 days" : range === "month" ? "in the last 30 days" : range === "year" ? "in the last 12 months"
    : `since ${first.toLocaleDateString(undefined, { month: "long", day: "numeric", ...(first.getFullYear() !== today.getFullYear() && { year: "numeric" }) })}`;
  return { unit, buckets, grid, streak, total, days, span, list: inRange };
}

/* ---- charts: plain SVG, drawn at the box's real width so text stays crisp */

const NICE_MIN = [5, 10, 15, 30, 60, 120, 180, 240, 300, 360, 480, 600, 720, 900, 1200, 1500, 1800, 2400, 3000, 3600, 6000, 12000, 30000, 60000];

function barPath(x, base, w, h) {
  const r = Math.min(4, w / 2, h);
  return `M${x},${base}V${base - h + r}Q${x},${base - h} ${x + r},${base - h}H${x + w - r}Q${x + w},${base - h} ${x + w},${base - h + r}V${base}Z`;
}

function barsSvg(buckets, goal, W, title) {
  const H = 168, top = 8, gutter = 34, base = H - 22, plotW = W - gutter;
  const maxMin = Math.max(60, goal / 60, ...buckets.map((b) => b.sec / 60));
  const step = NICE_MIN.find((n) => maxMin / n <= 3) || NICE_MIN[NICE_MIN.length - 1];
  const yMax = Math.ceil(maxMin / step) * step * 60;
  const y = (sec) => base - (sec / yMax) * (base - top);
  let out = "";
  for (let v = step * 60; v <= yMax; v += step * 60) {
    out += `<line class="grid" x1="0" x2="${plotW}" y1="${y(v)}" y2="${y(v)}"/>`;
    if (!goal || Math.abs(y(v) - y(goal)) > 12) out += `<text x="${W}" y="${y(v) + 3.5}" text-anchor="end">${fmtTotal(v)}</text>`;
  }
  out += `<line class="base" x1="0" x2="${plotW}" y1="${base}" y2="${base}"/>`;
  const slot = plotW / buckets.length;
  const bw = Math.min(24, Math.max(2, slot - Math.max(2, slot * 0.35)));
  buckets.forEach((b, i) => {
    const h = b.sec ? Math.max(2, base - y(b.sec)) : 0;
    out += `<g class="mark" style="--i:${i}" data-tip="${esc(fmtTotal(b.sec))}" data-sub="${esc(b.tip)}"><rect class="hit" x="${i * slot}" y="${top}" width="${slot}" height="${base - top}"/>`;
    if (h) out += `<path class="bar${goal && b.sec >= goal ? " met" : ""}" d="${barPath(i * slot + (slot - bw) / 2, base, bw, h)}"/>`;
    out += "</g>";
  });
  if (goal) out += `<line class="goal" x1="0" x2="${plotW}" y1="${y(goal)}" y2="${y(goal)}"/><text x="${W}" y="${y(goal) + 3.5}" text-anchor="end">goal</text>`;
  // Label as many buckets as fit, always including the latest.
  const lw = Math.max(...buckets.map((b) => b.label.length)) * 6.2 + 8;
  const every = Math.max(1, Math.ceil(lw / slot));
  for (let i = buckets.length - 1; i >= 0; i -= every) {
    const cx = i * slot + slot / 2;
    const [x, anchor] = cx - lw / 2 < 0 ? [Math.max(0, cx - slot / 2), "start"] : cx + lw / 2 > W ? [W, "end"] : [cx, "middle"];
    out += `<text x="${x}" y="${H - 5}" text-anchor="${anchor}">${esc(buckets[i].label)}</text>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(title)}">${out}</svg>`;
}

function heatSvg(grid, W) {
  const left = 16, cw = (W - left) / 24, rh = Math.max(15, Math.min(22, cw)), H = rh * 7 + 18;
  const max = Math.max(...grid.flat());
  let out = "";
  grid.forEach((row, r) => {
    out += `<text x="0" y="${r * rh + rh / 2 + 3.5}">${DAY_LETTERS[r]}</text>`;
    row.forEach((v, h) => {
      const level = v ? Math.ceil((v / max) * 4) : 0;
      out += `<rect class="cell l${level}" style="--d:${r + h}" x="${left + h * cw + 1}" y="${r * rh + 1}" width="${cw - 2}" height="${rh - 2}" rx="2" data-tip="${esc(fmtTotal(v))}" data-sub="${esc(`${weekdayName(r)}, ${fmtHour(h)}`)}"/>`;
    });
  });
  for (const h of [0, 6, 12, 18]) out += `<text x="${left + h * cw + 1}" y="${H - 4}">${esc(fmtHour(h))}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="When you burn, by weekday and hour">${out}</svg>`;
}

function renderStats() {
  $$("#st-range button").forEach((b) => b.classList.toggle("on", b.dataset.range === state.range));
  if (!burnsData) return;
  const { burns, now } = burnsData;
  $("#st-empty").hidden = burns.length > 0;
  $("#stats").hidden = burns.length === 0;
  if (!burns.length || $("#st-bars").clientWidth < 120) return; // not laid out yet
  hideTip();
  const st = crunch(state.range, burns, now);

  const fresh = statsKey !== `${state.range}|${burns.length}`;
  statsKey = `${state.range}|${burns.length}`;
  if (fresh) {
    FX.countUp($("#st-total"), st.total, (v) => fmtTotal(Math.round(v / 60) * 60));
    FX.replay($("#stats"), "drawn");
  } else {
    $("#stats").classList.remove("drawn");
    $("#st-total").textContent = fmtTotal(st.total);
  }
  $("#st-span").textContent = st.span;
  $("#st-avg").textContent = fmtDuration(st.total / st.days);
  $("#st-count").textContent = st.list.length;
  $("#st-count-label").textContent = st.list.length === 1 ? "burn" : "burns";
  $("#st-streak").textContent = st.streak;

  const goals = (state.settings && state.settings.goals) || {};
  const goal = st.unit === "day" ? goals.daily * 60 : st.unit === "week" ? goals.weekly * 60 : 0;
  const title = { day: "Per day", week: "Per week", month: "Per month" }[st.unit];
  $("#st-bars-title").textContent = title;
  $("#st-bars").innerHTML = barsSvg(st.buckets, goal || 0, $("#st-bars").clientWidth, `Burned ${title.toLowerCase()}`);
  $("#st-heat").innerHTML = heatSvg(st.grid, $("#st-heat").clientWidth);

  const byHour = st.grid[0].map((_, h) => st.grid.reduce((sum, row) => sum + row[h], 0));
  const byDay = st.grid.map((row) => row.reduce((sum, v) => sum + v, 0));
  const peak = (arr) => arr.indexOf(Math.max(...arr));
  $("#st-peak").textContent = st.total ? `Peak ${fmtHour(peak(byHour))} · ${weekdayName(peak(byDay), "long")}s` : "";

  // The log doubles as the table behind the charts: every burn, newest first.
  const rows = st.list.slice().reverse();
  let lastDay = "";
  $("#st-log").innerHTML = rows.slice(0, logShown).map((x) => {
    const d = dayOf(x.start);
    const day = isoDate(d) === lastDay ? "" : dayLabel(d);
    lastDay = isoDate(d);
    const time = x.live ? `${timeRange.format(new Date(x.start * 1000))} – now` : timeRange.formatRange(new Date(x.start * 1000), new Date(x.end * 1000));
    return `<li class="${x.live ? "live" : ""}"><span class="d">${esc(day)}</span><span class="t">${esc(time)}${x.early ? "<small>ended early</small>" : ""}</span><span class="len">${fmtDuration(x.end - x.start)}</span></li>`;
  }).join("");
  $("#st-more").hidden = rows.length <= logShown;
}

$$("#st-range button").forEach((b) => b.onclick = () => {
  state.range = b.dataset.range;
  ls.setItem("ignition.range", state.range);
  logShown = 12;
  renderStats();
});
$("#st-more").onclick = () => { logShown += 30; renderStats(); };

let statsWidth = 0;
window.addEventListener("resize", () => {
  const w = $("#st-bars").clientWidth;
  if (state.page === "stats" && w !== statsWidth) { statsWidth = w; renderStats(); }
});

/* ---- tooltip: hover on a computer, tap on a phone */

const tip = $("#chart-tip");
let tipMark = null;

function showTip(mark) {
  if (tipMark) tipMark.classList.remove("hot");
  tipMark = mark;
  mark.classList.add("hot");
  const b = document.createElement("b");
  b.textContent = mark.dataset.tip;
  const span = document.createElement("span");
  span.textContent = mark.dataset.sub;
  tip.replaceChildren(b, span);
  tip.hidden = false;
  const r = mark.getBoundingClientRect();
  const half = tip.offsetWidth / 2;
  const below = r.top - tip.offsetHeight - 8 < 0;
  tip.classList.toggle("below", below);
  tip.style.left = `${Math.min(window.innerWidth - half - 8, Math.max(half + 8, r.left + r.width / 2))}px`;
  tip.style.top = `${below ? r.bottom + 8 : r.top - 8}px`;
}

function hideTip() {
  tip.hidden = true;
  if (tipMark) tipMark.classList.remove("hot");
  tipMark = null;
}

for (const el of [$("#st-bars"), $("#st-heat")]) {
  el.addEventListener("pointerover", (e) => { const m = e.target.closest("[data-tip]"); if (m) showTip(m); });
  el.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") hideTip(); });
}
document.addEventListener("pointerdown", (e) => { if (!e.target.closest(".chart")) hideTip(); });
window.addEventListener("scroll", hideTip, { passive: true });

/* ---------------- routing & lifecycle ---------------- */

const TABS = ["ignite", "need", "want", "stats"];

/** The little bar under (or over, on a phone) the open tab, sliding across when you switch. */
function moveTabInd() {
  const a = $(".tabs a.active"), ind = $(".tab-ind");
  ind.classList.toggle("off", !a);
  if (!a) return;
  ind.style.transform = `translateX(${a.offsetLeft}px)`;
  ind.style.width = `${a.offsetWidth}px`;
}
window.addEventListener("resize", moveTabInd);
if (document.fonts) document.fonts.ready.then(moveTabInd);

// Ignite is home: the app opens on the button, and the lists sit behind it. Settings is a page of its
// own (#settings/<section>) that goes back to whichever tab you came from.
function route() {
  const [hash] = location.hash.slice(1).split("/");
  const page = [...TABS, "settings"].includes(hash) ? hash : "ignite";
  const was = state.page;
  state.page = page;
  if (TABS.includes(page)) state.lastTab = page;
  if (addDialog.open) addDialog.close();
  closeSortMenu();
  hideTip();
  $$(".page").forEach((p) => { p.hidden = p.id !== `page-${page}`; });
  $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === page));
  moveTabInd();
  if (was !== page) {
    const el = $(`#page-${page}`);
    FX.replay(el, "enter");
    clearTimeout(el.enterTimer);
    el.enterTimer = setTimeout(() => el.classList.remove("enter"), 1000);
    const h = $("h1", el);
    if (h && page !== "settings") FX.decode(h, h.textContent);
  }
  document.documentElement.dataset.page = page;
  FX.dim(page === "ignite" ? 1 : 0.45);
  requestAnimationFrame(() => { aimSky(); aimEmbers(); });
  $("#settings-btn").classList.toggle("active", page === "settings");
  $("#fab").hidden = page !== "need" && page !== "want";
  $("#fab").setAttribute("aria-label", page === "want" ? "Add something you want to do" : "Add something that needs doing");
  if (was !== page) window.scrollTo(0, 0);
  if (was === "settings" && page !== "settings") flushSettings();
  if (page === "settings") {
    if (was !== "settings" || !settingsDoc) openSettings().then(() => { if (state.page === "settings") goSettings(settingsSub()); });
    goSettings(settingsSub());
  }
  if (page === "stats") { renderStats(); loadBurns(); }
}

// Hooks for the Android app.
window.ignitionRefresh = () => {
  loadItems(); loadIgnite(); loadSettings();
  if (state.page === "settings" && NATIVE) loadAppInfo();
  if (state.page === "stats") loadBurns();
};
window.ignitionBack = () => {
  if (!$("#sort-menu").hidden) { closeSortMenu(); return true; }
  for (const d of [addDialog, mecoDialog]) if (d.open) { d.close(); return true; }
  if (state.page === "settings") { settingsBack(); return true; }
  if (state.page !== "ignite") { location.hash = "ignite"; return true; }
  return false;
};

window.addEventListener("hashchange", route);
document.addEventListener("keydown", (e) => {
  if (addDialog.open || mecoDialog.open || e.metaKey || e.ctrlKey || e.altKey) return;
  if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
  if (e.key === "n" || e.key === "+" || e.key === "a") { e.preventDefault(); openAdd(); }
  if (e.key === "1") location.hash = "ignite";
  if (e.key === "2") location.hash = "need";
  if (e.key === "3") location.hash = "want";
  if (e.key === "4") location.hash = "stats";
  if (e.key === ",") location.hash = "settings";
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { flushSettings(); return; }
  loadItems(); loadIgnite(); loadSettings();
  if (state.page === "stats") loadBurns();
});

paintSortButtons();
route();
loadItems();
loadIgnite();
loadSettings();
setInterval(() => { if (!document.hidden) loadIgnite(); }, 5000);
setInterval(() => { if (!document.hidden && !addDialog.open) loadItems(); }, 30000);
setInterval(() => { if (!document.hidden && state.page === "stats") loadBurns(); }, 30000);
// The clock ticks just after each whole second of the server's time, which is when the readout's digits
// change; a free-running 1 s interval drifts across that edge and shows a second twice, then skips one.
(function tick() {
  const { phase, session } = state.ignite;
  if (phase !== "idle" || arming) renderIgnite();
  const edge = phase === "countdown" ? session.starts_at : phase === "burning" ? session.ends_at : null;
  if (edge && serverNow() >= edge && !document.hidden) loadIgnite();
  const t = serverNow();
  setTimeout(tick, (Math.floor(t) + 1 - t) * 1000 + 20);
})();
setInterval(() => renderLists(), 60000);
setInterval(() => {
  if (document.hidden || state.page !== "settings" || !appInfo) return;
  if (settingsPage === "sync") paintSyncStatus();
  if (settingsPage === "home") paintSummaries();
}, 5000);
