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
if (NATIVE) document.documentElement.classList.add("in-app");

const state = {
  items: [],
  ignite: { phase: "idle", active: false, enforcers: [] },
  clockOffset: 0,
  page: "need",
  expanded: new Set(),
  pendingDelete: new Map(),
  rendered: new Set(),
  itemsJson: "",
  sort: { need: ls.getItem("ignition.sort.need") || "smart", want: ls.getItem("ignition.sort.want") || "smart" },
};

async function api(method, path, body) {
  if (NATIVE) {
    const res = JSON.parse(NATIVE.request(method, path, body ? JSON.stringify(body) : ""));
    if (res.status >= 400) throw new Error((res.body && res.body.error) || `Error ${res.status}`);
    return res.body;
  }
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
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
  if (row && nowDone) row.classList.add("is-done");
  state.expanded.delete(it.id);
  setTimeout(() => patchItem(it.id, { done: nowDone }), nowDone ? 260 : 0);
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

const RING = 2 * Math.PI * 92;
const serverNow = () => Date.now() / 1000 + state.clockOffset;
let arming = null;

function setIgnite(s, quiet) {
  const was = state.ignite;
  state.clockOffset = s.now - Date.now() / 1000;
  state.ignite = s;
  if (!quiet && was.session && was.phase === "countdown" && s.phase === "burning" && s.session && s.session.id === was.session.id) {
    toast("Liftoff.");
    if (navigator.vibrate) navigator.vibrate([60, 60, 200]);
  }
  if (!quiet && was.phase === "burning" && s.phase === "idle" && !state.stopping && serverNow() >= was.session.ends_at - 2) {
    toast(`MECO. ${was.session.minutes} min burned.`, null, null, 8000);
    if (navigator.vibrate) navigator.vibrate([120, 80, 120]);
  }
  state.stopping = false;
  renderIgnite();
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

function renderIgnite() {
  const s = state.ignite;
  const sess = s.session;
  const t = serverNow();
  const dial = $("#dial");
  const phase = s.phase || (s.active ? "burning" : "idle");
  dial.classList.toggle("active", phase === "burning");
  dial.classList.toggle("countdown", phase === "countdown");
  dial.classList.toggle("arming", !!arming);
  $("#launch-params").hidden = phase !== "idle";
  $("#session-info").hidden = phase === "idle";
  const fill = $("#ring-fill");
  fill.style.strokeDasharray = RING;
  let tabLabel = "Ignite", barFrac = null;

  if (phase === "burning") {
    const remaining = sess.ends_at - t;
    const total = sess.ends_at - sess.starts_at;
    $("#ignite-label").textContent = fmtClock(remaining);
    $("#ignite-sub").textContent = "burning";
    fill.style.strokeDashoffset = RING * (1 - remaining / total);
    $("#until").textContent = `Locked until ${fmtTime(sess.ends_at)}`;
    $("#extend").hidden = false;
    $("#stop-btn").hidden = !s.can_stop;
    $("#locked-note").hidden = !!s.can_stop;
    $("#scrub-btn").hidden = true;
    tabLabel = fmtClock(remaining);
    barFrac = 1 - remaining / total;
  } else if (phase === "countdown") {
    const left = sess.starts_at - t;
    const total = Math.max(1, sess.starts_at - sess.created_at);
    $("#ignite-label").textContent = `T–${fmtClock(left)}`;
    $("#ignite-sub").textContent = "tap to ignite now";
    fill.style.strokeDashoffset = RING * (1 - Math.min(1, left / total));
    $("#until").textContent = `Ignition at ${fmtTime(sess.starts_at)} · ${fmtEstimate(sess.minutes)} burn`;
    $("#extend").hidden = true;
    $("#stop-btn").hidden = true;
    $("#locked-note").hidden = true;
    $("#scrub-btn").hidden = false;
    tabLabel = `T–${fmtClock(left)}`;
  } else if (arming) {
    $("#ignite-label").textContent = arming.count;
    $("#ignite-sub").textContent = "tap to scrub";
    fill.style.strokeDashoffset = RING;
  } else {
    $("#ignite-label").textContent = "IGNITE";
    $("#ignite-sub").textContent = "";
    fill.style.strokeDashoffset = RING;
  }

  const bar = $("#burnbar");
  bar.hidden = barFrac == null;
  if (barFrac != null) $("div", bar).style.width = `${100 * barFrac}%`;
  const tab = $('.tabs a[data-tab="ignite"]');
  tab.classList.toggle("burning", phase !== "idle");
  $("#tab-ignite-label").textContent = tabLabel;

  renderBurnStats(s.stats);
  renderNextSchedule(s.next_schedule, phase);

  $("#enforcers").innerHTML = (s.enforcers || []).map((e) => {
    const stale = e.age > 30;
    const armed = !stale && e.status && e.status.armed;
    const label = stale ? "offline" : armed ? "locked" : phase === "burning" ? "arming…" : "ready";
    const err = !stale && e.status && e.status.error ? ` title="${esc(e.status.error)}"` : "";
    return `<li class="${stale ? "stale" : armed ? "armed" : "ready"}"${err}>${esc(e.host)} · ${label}${err ? " ⚠" : ""}</li>`;
  }).join("");
}

function renderBurnStats(st) {
  const box = $("#burn-stats");
  if (!st) { box.innerHTML = ""; return; }
  const goals = st.goals || {};
  const row = (label, sec, goalMin, unit) => {
    if (!goalMin) return "";
    const frac = Math.min(1, sec / 60 / goalMin);
    const val = unit === "h" ? `${(sec / 3600).toFixed(1).replace(/\.0$/, "")} / ${(goalMin / 60).toFixed(1).replace(/\.0$/, "")} h` : `${Math.round(sec / 60)} / ${goalMin} min`;
    return `<div class="goal${frac >= 1 ? " met" : ""}"><span class="goal-label">${label}</span><span class="goal-bar"><i style="width:${frac * 100}%"></i></span><span class="goal-val">${val}</span></div>`;
  };
  const rows = row("Today", st.today, goals.daily, "m") + row("This week", st.week, goals.weekly, "h");
  box.innerHTML = rows || `<p class="burned">Burned today ${fmtDuration(st.today)} · this week ${fmtDuration(st.week)} · <button type="button" class="link" id="set-goals">set goals</button></p>`;
  const sg = $("#set-goals");
  if (sg) sg.onclick = openSettings;
}

function renderNextSchedule(ns, phase) {
  const el = $("#next-sched");
  if (!ns || phase !== "idle") { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `Next launch ${esc(fmtWhen(ns.at))} · ${fmtEstimate(ns.minutes)} <button type="button" class="link">skip</button>`;
  $("button", el).onclick = () => igniteAction("/api/ignite/skip", { schedule_id: ns.schedule_id, date: ns.date }, "Launch window skipped.");
}

$("#ignite-btn").addEventListener("click", () => {
  const phase = state.ignite.phase;
  if (phase === "burning") return;
  if (phase === "countdown") { igniteAction("/api/ignite/now", null, "Liftoff."); return; }
  if (arming) {
    clearInterval(arming.timer);
    arming = null;
    renderIgnite();
    return;
  }
  const minutes = parseInt($("#minutes").value, 10);
  const delay = parseInt($("#delay").value || "0", 10) || 0;
  if (!(minutes >= 1 && minutes <= 720)) { toast("Minutes: 1–720"); $("#minutes").focus(); return; }
  if (!(delay >= 0 && delay <= 1440)) { toast("T-minus: 0–1440 minutes"); $("#delay").focus(); return; }
  ls.setItem("ignition.minutes", String(minutes));
  ls.setItem("ignition.delay", String(delay));
  arming = { count: 3, minutes, delay };
  arming.timer = setInterval(async () => {
    arming.count -= 1;
    if (arming.count > 0) { renderIgnite(); return; }
    clearInterval(arming.timer);
    const { minutes: m, delay: d } = arming;
    arming = null;
    try {
      setIgnite(await api("POST", "/api/ignite", { minutes: m, delay: d }), true);
      toast(d ? `Countdown started. Ignition in T-minus ${d}.` : "Liftoff.");
      if (navigator.vibrate) navigator.vibrate(60);
    } catch (e) {
      toast(e.message);
      renderIgnite();
    }
  }, 1000);
  renderIgnite();
});

for (const [id, key] of [["#minutes", "ignition.minutes"], ["#delay", "ignition.delay"]]) {
  const saved = ls.getItem(key);
  if (saved) $(id).value = saved;
  $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") $("#ignite-btn").click(); });
}

$$("[data-extend]").forEach((b) => b.addEventListener("click", () =>
  igniteAction("/api/ignite/extend", { minutes: Number(b.dataset.extend) }, `Extended ${b.dataset.extend} min.`)));
$("#scrub-btn").addEventListener("click", () => igniteAction("/api/ignite/scrub", null, "Launch scrubbed."));

$("#info-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  const pop = $("#info-pop");
  pop.hidden = !pop.hidden;
  $("#info-btn").setAttribute("aria-expanded", String(!pop.hidden));
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#info-pop")) { $("#info-pop").hidden = true; $("#info-btn").setAttribute("aria-expanded", "false"); }
});

(() => {
  const btn = $("#stop-btn");
  let t;
  const cancel = () => { clearTimeout(t); btn.classList.remove("holding"); };
  btn.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    btn.classList.add("holding");
    t = setTimeout(async () => {
      btn.classList.remove("holding");
      state.stopping = true;
      try { setIgnite(await api("POST", "/api/ignite/stop"), true); toast("Engines off. Burn ended early."); }
      catch (err) { state.stopping = false; toast(err.message); }
    }, 2000);
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => btn.addEventListener(ev, cancel));
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
})();

/* ---------------- settings ---------------- */

const settingsDialog = $("#settings-dialog");
const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
let appInfo = null;

function scheduleEl(sc) {
  const li = $("#tpl-schedule").content.firstElementChild.cloneNode(true);
  li.dataset.id = sc.id;
  $(".sched-time", li).value = sc.time;
  $(".sched-min", li).value = sc.minutes;
  $(".sched-on", li).checked = sc.enabled !== false;
  $(".sched-lead-min", li).value = sc.lead ?? 5;
  $(".sched-days", li).innerHTML = DAY_LETTERS.map((l, i) =>
    `<button type="button" class="${sc.days.includes(i + 1) ? "on" : ""}" data-day="${i + 1}" aria-label="day ${i + 1}">${l}</button>`).join("");
  $$(".sched-days button", li).forEach((b) => b.onclick = () => b.classList.toggle("on"));
  $(".sched-del", li).onclick = () => li.remove();
  return li;
}

function readSchedules() {
  return $$("#schedules .schedule").map((li) => ({
    id: li.dataset.id,
    time: $(".sched-time", li).value || "09:00",
    minutes: parseInt($(".sched-min", li).value, 10) || 50,
    enabled: $(".sched-on", li).checked,
    lead: parseInt($(".sched-lead-min", li).value || "0", 10) || 0,
    days: $$(".sched-days button.on", li).map((b) => Number(b.dataset.day)),
  }));
}

async function openSettings() {
  $("#settings-msg").textContent = "";
  try {
    const s = await api("GET", "/api/settings");
    $("#goal-daily").value = s.goals.daily || "";
    $("#goal-weekly").value = s.goals.weekly ? +(s.goals.weekly / 60).toFixed(1) : "";
    $("#schedules").replaceChildren(...s.schedules.map(scheduleEl));
  } catch (e) { $("#settings-msg").textContent = e.message; }
  if (NATIVE) await loadAppInfo();
  settingsDialog.showModal();
}

async function loadAppInfo() {
  $("#app-settings").hidden = false;
  try {
    appInfo = await api("GET", "/native/app");
    $("#server-url").value = appInfo.server_url || "";
    paintSyncStatus();
    $("#dnd-toggle").checked = !!appInfo.dnd.enabled;
    $("#dnd-hint").textContent = appInfo.dnd.enabled && !appInfo.dnd.granted ? "Needs Do Not Disturb access. Tap the toggle again to grant it." : "";
    $("#notif-btn").textContent = appInfo.notifications.granted ? "Allowed" : "Allow";
    $("#notif-btn").disabled = !!appInfo.notifications.granted;
  } catch (e) { $("#sync-status").textContent = e.message; }
}

function paintSyncStatus() {
  const s = appInfo && appInfo.sync;
  let text = "Not syncing. Everything stays on this phone.";
  if (appInfo && appInfo.server_url) {
    text = s && s.last_ok ? `Synced ${fmtWhen(s.last_ok)}` : "Not synced yet";
    if (s && s.last_error && (!s.last_ok || s.last_error_at > s.last_ok)) text += ` · last try failed: ${s.last_error}`;
  }
  $("#sync-status").textContent = text;
}

$("#settings-btn").onclick = openSettings;
$("#settings-close").onclick = () => settingsDialog.close();
settingsDialog.addEventListener("click", (e) => { if (e.target === settingsDialog) settingsDialog.close(); });
$("#add-schedule").onclick = () => {
  $("#schedules").append(scheduleEl({ id: randomId(), time: "09:00", minutes: 50, lead: 5, enabled: true, days: [1, 2, 3, 4, 5] }));
};
$("#settings-save").onclick = async () => {
  const body = {
    goals: {
      daily: parseInt($("#goal-daily").value || "0", 10) || 0,
      weekly: Math.round((parseFloat($("#goal-weekly").value || "0") || 0) * 60),
    },
    schedules: readSchedules(),
  };
  try {
    await api("PUT", "/api/settings", body);
    settingsDialog.close();
    toast("Saved.");
    loadIgnite();
  } catch (e) { $("#settings-msg").textContent = e.message; }
};
$("#server-test").onclick = async () => {
  $("#sync-status").textContent = "Checking…";
  await new Promise((r) => setTimeout(r, 50)); // let "Checking…" paint before the blocking native call
  try {
    appInfo = await api("POST", "/native/app/server", { server_url: $("#server-url").value.trim() });
    paintSyncStatus();
    loadItems(); loadIgnite();
  } catch (e) { $("#sync-status").textContent = e.message; }
};
$("#dnd-toggle").onchange = async (e) => {
  try { appInfo = await api("PUT", "/native/app", { dnd_enabled: e.target.checked }); } catch (err) { toast(err.message); }
  loadAppInfo();
};
$("#notif-btn").onclick = async () => { try { await api("POST", "/native/app/notifications"); } catch (e) { toast(e.message); } };

/* ---------------- routing & lifecycle ---------------- */

function route() {
  const page = ["need", "want", "ignite"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "need";
  state.page = page;
  if (addDialog.open) addDialog.close();
  closeSortMenu();
  $$(".page").forEach((p) => { p.hidden = p.id !== `page-${page}`; });
  $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === page));
  $("#fab").setAttribute("aria-label", page === "want" ? "Add something you want to do" : "Add something that needs doing");
}

// Hooks for the Android app.
window.ignitionRefresh = () => { loadItems(); loadIgnite(); if (settingsDialog.open && NATIVE) loadAppInfo(); };
window.ignitionBack = () => {
  if (!$("#sort-menu").hidden) { closeSortMenu(); return true; }
  for (const d of [addDialog, settingsDialog]) if (d.open) { d.close(); return true; }
  if (state.page !== "need") { location.hash = "need"; return true; }
  return false;
};

window.addEventListener("hashchange", route);
document.addEventListener("keydown", (e) => {
  if (addDialog.open || settingsDialog.open || e.metaKey || e.ctrlKey || e.altKey) return;
  if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
  if (e.key === "n" || e.key === "+" || e.key === "a") { e.preventDefault(); openAdd(); }
  if (e.key === "1") location.hash = "need";
  if (e.key === "2") location.hash = "want";
  if (e.key === "3") location.hash = "ignite";
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { loadItems(); loadIgnite(); }
});

paintSortButtons();
route();
loadItems();
loadIgnite();
setInterval(() => { if (!document.hidden) loadIgnite(); }, 5000);
setInterval(() => { if (!document.hidden && !addDialog.open) loadItems(); }, 30000);
setInterval(() => {
  const { phase, session } = state.ignite;
  if (phase !== "idle" || arming) renderIgnite();
  const edge = phase === "countdown" ? session.starts_at : phase === "burning" ? session.ends_at : null;
  if (edge && serverNow() >= edge && !document.hidden) loadIgnite();
}, 1000);
setInterval(() => renderLists(), 60000);
