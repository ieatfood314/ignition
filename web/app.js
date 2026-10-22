"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  items: [],
  ignite: { active: false, enforcers: [] },
  clockOffset: 0,
  page: "need",
  expanded: new Set(),
  pendingDelete: new Map(),
};

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

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

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ICON_CHECK = '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const ICON_PLUS = '<svg viewBox="0 0 24 24"><path d="M12 6v12M6 12h12"/></svg>';
const ICON_NOTE = '<svg viewBox="0 0 24 24"><path d="M5 4h14v16H5zM9 9h6M9 13h6M9 17h3"/></svg>';

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

/* ---------------- lists ---------------- */

function sortItems(list, items) {
  if (list === "need") {
    return items.sort((a, b) =>
      (a.due ? 0 : 1) - (b.due ? 0 : 1) ||
      (a.due && b.due ? a.due.localeCompare(b.due) : 0) ||
      b.priority - a.priority ||
      a.created_at - b.created_at);
  }
  return items.sort((a, b) => b.priority - a.priority || a.created_at - b.created_at);
}

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
  li.className = "item" + (it.done_at ? " is-done" : "");
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
  renderUpNext();
}

function renderUpNext() {
  const next = sortItems("need", state.items.filter((i) => i.list === "need" && !i.done_at && !state.pendingDelete.has(i.id))).slice(0, 3);
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
    state.items = await api("GET", "/api/items");
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

function setIgnite(s) {
  const was = state.ignite;
  state.clockOffset = s.now - Date.now() / 1000;
  state.ignite = s;
  if (was.active && !s.active && was.session_id && !state.stopping && serverNow() >= was.ends_at - 2) {
    toast(`Session complete. ${was.minutes} min.`, null, null, 8000);
    if (navigator.vibrate) navigator.vibrate([120, 80, 120]);
  }
  state.stopping = false;
  renderIgnite();
}

async function loadIgnite() {
  try { setIgnite(await api("GET", "/api/ignite")); } catch (e) { console.error(e); }
}

function renderIgnite() {
  const s = state.ignite;
  const dial = $("#dial");
  const remaining = s.active ? s.ends_at - serverNow() : 0;
  dial.classList.toggle("active", !!s.active);
  dial.classList.toggle("arming", !!arming);
  $("#duration").hidden = !!s.active;
  $("#session-info").hidden = !s.active;
  const fill = $("#ring-fill");
  fill.style.strokeDasharray = RING;

  if (s.active) {
    $("#ignite-label").textContent = fmtClock(remaining);
    $("#ignite-sub").textContent = "burning";
    fill.style.strokeDashoffset = RING * (1 - remaining / (s.minutes * 60));
    const until = new Date(s.ends_at * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    $("#until").textContent = `Locked until ${until}`;
    $("#stop-btn").hidden = !s.can_stop;
    $("#locked-note").hidden = !!s.can_stop;
  } else if (arming) {
    $("#ignite-label").textContent = arming.count;
    $("#ignite-sub").textContent = "tap to cancel";
    fill.style.strokeDashoffset = RING;
  } else {
    $("#ignite-label").textContent = "IGNITE";
    $("#ignite-sub").textContent = "";
    fill.style.strokeDashoffset = RING;
  }

  const bar = $("#burnbar");
  bar.hidden = !s.active;
  if (s.active) $("div", bar).style.width = `${100 * (1 - remaining / (s.minutes * 60))}%`;
  const tab = $('.tabs a[data-tab="ignite"]');
  tab.classList.toggle("burning", !!s.active);
  $("#tab-ignite-label").textContent = s.active ? fmtClock(remaining) : "Ignite";

  $("#enforcers").innerHTML = (s.enforcers || []).map((e) => {
    const stale = e.age > 30;
    const armed = !stale && e.status && e.status.armed;
    const label = stale ? "offline" : armed ? "locked" : s.active ? "arming…" : "ready";
    const err = !stale && e.status && e.status.error ? ` title="${esc(e.status.error)}"` : "";
    return `<li class="${stale ? "stale" : armed ? "armed" : "ready"}"${err}>${esc(e.host)} · ${label}${err ? " ⚠" : ""}</li>`;
  }).join("");
}

$("#ignite-btn").addEventListener("click", () => {
  if (state.ignite.active) return;
  if (arming) {
    clearInterval(arming.timer);
    arming = null;
    renderIgnite();
    return;
  }
  const minutes = parseInt($("#minutes").value, 10);
  if (!(minutes >= 1 && minutes <= 720)) { toast("Minutes: 1–720"); $("#minutes").focus(); return; }
  localStorage.setItem("ignition.minutes", String(minutes));
  arming = { count: 3, minutes };
  arming.timer = setInterval(async () => {
    arming.count -= 1;
    if (arming.count > 0) { renderIgnite(); return; }
    clearInterval(arming.timer);
    const m = arming.minutes;
    arming = null;
    try {
      setIgnite(await api("POST", "/api/ignite", { minutes: m }));
      if (navigator.vibrate) navigator.vibrate(60);
    } catch (e) {
      toast(e.message);
      renderIgnite();
    }
  }, 1000);
  renderIgnite();
});

const savedMinutes = localStorage.getItem("ignition.minutes");
if (savedMinutes) $("#minutes").value = savedMinutes;
$("#minutes").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#ignite-btn").click(); });

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
      try { setIgnite(await api("POST", "/api/ignite/stop")); toast("Session ended early."); }
      catch (err) { state.stopping = false; toast(err.message); }
    }, 2000);
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => btn.addEventListener(ev, cancel));
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
})();

/* ---------------- routing & lifecycle ---------------- */

function route() {
  const page = ["need", "want", "ignite"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "need";
  state.page = page;
  if (addDialog.open) addDialog.close();
  $$(".page").forEach((p) => { p.hidden = p.id !== `page-${page}`; });
  $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === page));
  $("#fab").setAttribute("aria-label", page === "want" ? "Add something you want to do" : "Add something that needs doing");
}

window.addEventListener("hashchange", route);
document.addEventListener("keydown", (e) => {
  if (addDialog.open || e.metaKey || e.ctrlKey || e.altKey) return;
  if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
  if (e.key === "n" || e.key === "+" || e.key === "a") { e.preventDefault(); openAdd(); }
  if (e.key === "1") location.hash = "need";
  if (e.key === "2") location.hash = "want";
  if (e.key === "3") location.hash = "ignite";
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { loadItems(); loadIgnite(); }
});

route();
loadItems();
loadIgnite();
setInterval(() => { if (!document.hidden) loadIgnite(); }, 5000);
setInterval(() => { if (!document.hidden && !addDialog.open) loadItems(); }, 30000);
setInterval(() => { if (state.ignite.active) renderIgnite(); }, 1000);
setInterval(() => renderLists(), 60000);
