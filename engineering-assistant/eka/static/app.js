// Engineering Knowledge Assistant — client.
// No framework and no build step. Every piece of document or user text is
// inserted with textContent; nothing from the server is parsed as HTML.
"use strict";
import { BRAND } from "./brand/brand.js";
import { LumaMascot, preloadMascot, staticMascot } from "./luma/mascot.js";
preloadMascot();

const $ = (id) => document.getElementById(id);
const h = (tag, cls, text) => {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined && text !== null) el.textContent = text;
  return el;
};
const icon = (name, cls) => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  if (cls) svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
};
const btn = (cls, label, iconName, title) => {
  const b = h("button", cls);
  b.type = "button";
  if (iconName) b.append(icon(iconName));
  if (label) b.append(document.createTextNode(label));
  if (title) { b.title = title; b.setAttribute("aria-label", title); }
  return b;
};
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const desktop = () => innerWidth > 1180;
const phone = () => innerWidth <= 880;
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const S = {
  token: teamKey(),
  health: null,
  docs: [],
  convs: [],
  cid: null,
  scope: new Set(),
  busy: false,
  abort: null,
  viewer: null,
  pollTimer: null,
  attachments: new Map(),   // doc id or temp id -> {name, el, docId}
  followScroll: true,
};

// ── API ──────────────────────────────────────────────────────────────────
// A shared team link carries its key after "#key=" (never sent to the server). It is kept in this
// browser and removed from the address bar, so the address can be bookmarked or shown safely.
function teamKey() {
  const m = location.hash.match(/^#key=([^&]+)/);
  if (m) {
    const key = decodeURIComponent(m[1]);
    store.set("eka-key", key);
    history.replaceState(null, "", location.pathname + location.search + "#/");
    return key;
  }
  return store.get("eka-key", "") || sessionStorage.getItem("eka-token") || "";
}
// This browser's id: on a shared server each person's conversations stay in their own list.
function clientId() {
  let id = store.get("eka-client", "");
  if (!/^[A-Za-z0-9_-]{8,64}$/.test(id)) {
    id = (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/[^A-Za-z0-9_-]/g, "");
    store.set("eka-client", id);
  }
  return id;
}
function authed(url) {
  if (!S.token) return url;
  const [path, hash] = url.split("#");
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(S.token) + (hash ? "#" + hash : "");
}
async function api(path, options = {}) {
  const headers = { "X-EKA-Client": clientId(), ...(options.headers || {}) };
  if (S.token) headers.Authorization = "Bearer " + S.token;
  let res;
  try {
    res = await fetch(path, { ...options, headers });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new Error("The server is not reachable. Check that it is running.");
  }
  if (res.status === 401 && askToken()) return api(path, options);
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : `Request failed (HTTP ${res.status}).`);
  }
  return data;
}
function askToken() {
  const t = prompt("Enter the team key (it is in the link you were given):");
  if (!t) return false;
  S.token = t.trim();
  store.set("eka-key", S.token);
  return true;
}

// ── theme: Light, Dark, System (the ATLAS mechanism) ─────────────────────
const darkQuery = matchMedia("(prefers-color-scheme: dark)");
function applyTheme(choice, animate) {
  const root = document.documentElement;
  const dark = choice === "dark" || (choice === "system" && darkQuery.matches);
  if (animate && document.startViewTransition && !reduced() && root.dataset.theme !== (dark ? "dark" : "light")) {
    document.startViewTransition(() => applyTheme(choice, false));
    return;
  }
  root.classList.add("theme-switching");
  root.dataset.theme = dark ? "dark" : "light";
  root.dataset.themeChoice = choice;
  document.querySelector('meta[name="theme-color"]').content = dark ? BRAND.themeColor.dark : BRAND.themeColor.light;
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("theme-switching")));
  document.querySelectorAll("[data-theme-set]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeSet === choice)));
  try { localStorage.setItem("eka-theme", choice); } catch { /* ignore */ }
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-theme-set]");
  if (b) applyTheme(b.dataset.themeSet, true);
});
darkQuery.addEventListener("change", () => { if ((document.documentElement.dataset.themeChoice || "system") === "system") applyTheme("system"); });
applyTheme(document.documentElement.dataset.themeChoice || "system");

// ── small UI helpers ─────────────────────────────────────────────────────
let toastTimer;
function toast(text) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const t = h("div", "toast", text);
  t.setAttribute("role", "status");
  document.body.append(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), 2200);
}
let popEl = null, popOwner = null;
function closePop() {
  if (popEl) { popEl.remove(); popEl = null; }
  if (popOwner) { popOwner.setAttribute("aria-expanded", "false"); popOwner = null; }
}
function openPop(owner, build, align = "left") {
  if (popOwner === owner) { closePop(); return; }
  closePop();
  const pop = h("div", "pop");
  pop.setAttribute("role", "menu");
  build(pop);
  document.body.append(pop);
  const r = owner.getBoundingClientRect();
  const w = pop.offsetWidth, ph = pop.offsetHeight;
  let left = align === "right" ? r.right - w : r.left;
  left = Math.max(12, Math.min(left, innerWidth - w - 12));
  let top = r.bottom + 6;
  if (top + ph > innerHeight - 12) top = Math.max(12, r.top - ph - 6);
  pop.style.left = left + "px";
  pop.style.top = top + "px";
  popEl = pop; popOwner = owner;
  owner.setAttribute("aria-expanded", "true");
  const first = pop.querySelector("button, input");
  if (first) first.focus();
}
document.addEventListener("pointerdown", (e) => {
  if (popEl && !popEl.contains(e.target) && !(popOwner && popOwner.contains(e.target))) closePop();
});
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const docTitle = (d) => (d ? d.title || d.filename : "Document");
const searchable = (d) => d.status === "ready" || d.status === "partial";

// Identifiers (part numbers, models, thread sizes) are set in the monospace face
// so 1R-0750 and M16x2.0 read as data, as ATLAS does for references.
const ID_RE = /\b(?=[A-Za-z0-9./-]*\d)(?=[A-Za-z0-9./-]*[A-Za-z])[A-Za-z0-9]+(?:[-./][A-Za-z0-9]+)*\b/g;
function rich(text, into) {
  const frag = into || document.createDocumentFragment();
  let last = 0;
  for (const m of String(text).matchAll(ID_RE)) {
    if (m.index > last) frag.append(document.createTextNode(text.slice(last, m.index)));
    frag.append(h("span", "id", m[0]));
    last = m.index + m[0].length;
  }
  if (last < text.length) frag.append(document.createTextNode(text.slice(last)));
  return frag;
}
// Inline markdown: **bold**, `code`; everything else is text.
function inline(text) {
  const frag = document.createDocumentFragment();
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  for (const m of String(text).matchAll(re)) {
    if (m.index > last) rich(text.slice(last, m.index), frag);
    const tok = m[0];
    frag.append(tok.startsWith("**") ? rich(tok.slice(2, -2), h("strong")) : h("code", "", tok.slice(1, -1)));
    last = m.index + tok.length;
  }
  if (last < text.length) rich(text.slice(last), frag);
  return frag;
}
// Block markdown: headings, lists, tables, code fences, paragraphs.
function md(text) {
  const frag = document.createDocumentFragment();
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.startsWith("```")) {
      const code = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      frag.append(h("pre", "", code.join("\n")));
      continue;
    }
    const head = line.match(/^#{1,4}\s+(.*)/);
    if (head) { const d = h("div", "ah"); d.append(inline(head[1])); frag.append(d); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
      const cells = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r)).map((r) => r.trim().slice(1, -1).split("|").map((c) => c.trim()));
      if (cells.length) frag.append(table(cells[0], cells.slice(1)));
      continue;
    }
    const bullet = /^\s*[-*•]\s+/, num = /^\s*\d{1,2}[.)]\s+/;
    if (bullet.test(line) || num.test(line)) {
      const ordered = num.test(line);
      const list = h(ordered ? "ol" : "ul");
      while (i < lines.length && (ordered ? num : bullet).test(lines[i])) {
        const li = h("li");
        li.append(inline(lines[i].replace(ordered ? num : bullet, "")));
        list.append(li);
        i++;
      }
      frag.append(list);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !bullet.test(lines[i]) && !num.test(lines[i]) && !lines[i].startsWith("```")) para.push(lines[i++]);
    const p = h("p");
    para.forEach((l, k) => { if (k) p.append(h("br")); p.append(inline(l)); });
    frag.append(p);
  }
  return frag;
}
function table(head, rows) {
  const wrap = h("div", "tbl"), t = h("table"), tr = t.createTHead().insertRow();
  head.forEach((c) => tr.append(h("th", "", c)));
  const body = t.createTBody();
  rows.forEach((r) => {
    const row = body.insertRow();
    r.forEach((c) => row.insertCell().append(inline(c)));
  });
  wrap.append(t);
  return wrap;
}

// ── health and settings ──────────────────────────────────────────────────
async function loadHealth() {
  try {
    S.health = await api("/api/health");
  } catch (e) {
    $("modelTxt").textContent = $("modelPillTxt").textContent = e.message;
    $("modelRow").dataset.s = $("modelPill").dataset.s = "bad";
    return;
  }
  const hl = S.health;
  const answers = hl.generator === "extractive" ? "Quoted answers" : (hl.llm_model || hl.generator) + " (local)";
  const retrieval = hl.semantic_search ? "semantic search" : "keyword search";
  $("modelTxt").textContent = `${answers} · ${retrieval}`;
  if (hl.org_logo && $("orgLogo").hidden) {
    $("orgLogo").src = typeof hl.org_logo === "string" ? hl.org_logo : BRAND.orgLogo;
    $("orgLogo").alt = BRAND.org;
    $("orgLogo").hidden = false;
  }
  $("modelRow").dataset.s = $("modelPill").dataset.s = hl.generator_ready === false ? "bad" : hl.semantic_search ? "ok" : "kw";
  $("modelRow").title = hl.retrieval_mode;
  // the composer names what will write the answer: the local model, or quoted answers without one
  $("modelPillTxt").textContent = hl.generator === "extractive" ? "Quoted answers" : hl.llm_model || hl.generator;
  $("modelPill").title = `Answers: ${answers}. Retrieval: ${hl.retrieval_mode}`;
  if (!$("viewLib").hidden) renderSources();
}
function fillSettings() {
  const hl = S.health || {};
  const kv = (el, pairs) => {
    el.replaceChildren();
    pairs.forEach(([k, v]) => { el.append(h("dt", "", k), h("dd", "", v)); });
  };
  const llm = hl.generator && hl.generator !== "extractive";
  kv($("setAnswers"), [
    ["Method", llm ? "Local language model via Ollama" : "Extractive: sentences, steps and table rows quoted from the documents"],
    ["Model", llm ? hl.llm_model : "None (no language model is used)"],
    ["Status", llm ? (hl.generator_ready ? "Ready" : "Unavailable: " + (hl.generator_detail || "")) : "Ready"],
  ]);
  $("setAnswersNote").textContent = llm
    ? "Model output is checked claim by claim: anything citing a passage that wasn't retrieved, or stating a value its passage doesn't contain, is removed."
    : "To phrase answers with a local Qwen model, install Ollama, pull a model yourself (e.g. ollama pull qwen2.5:7b-instruct), and start the server with EKA_GENERATOR=ollama.";
  kv($("setRetrieval"), [
    ["Mode", hl.retrieval_mode || "—"],
    ["Semantic search", hl.semantic_search ? "On" : "Off"],
    ["OCR", hl.ocr === "tesseract" ? "Tesseract (scanned pages)" : "Unavailable: scanned pages cannot be read"],
  ]);
  $("setRetrievalNote").textContent = hl.semantic_search ? ""
    : "Semantic search is off because no embedding model is installed locally; questions must share words with the documents. To turn it on: pip install -r requirements-optional.txt and start once with EKA_EMBEDDER=fastembed (downloads a 65 MB model).";
  kv($("setLibrary"), [
    ["Documents", `${hl.searchable_documents ?? 0} searchable of ${hl.documents ?? 0}`],
    ["Indexed passages", String(hl.indexed_passages ?? 0)],
  ]);
}
function openSettings() {
  fillSettings();
  document.querySelectorAll("[data-theme-set]").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.themeSet === (document.documentElement.dataset.themeChoice || "system"))));
  $("settings").showModal();
  loadHealth().then(fillSettings);
}
$("openSettings").addEventListener("click", openSettings);
$("modelRow").addEventListener("click", openSettings);
$("modelPill").addEventListener("click", openSettings);
document.querySelectorAll("[data-close-dialog]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
$("settings").addEventListener("click", (e) => { if (e.target === $("settings")) $("settings").close(); });

// ── sidebar: collapse on desktop, drawer on phones ───────────────────────
const app = $("app");
function setSide(open, remember = true) {
  if (phone()) { app.classList.toggle("drawer", open); return; }
  app.classList.toggle("side-closed", !open);
  if (remember) store.set("eka-side", open);
}
$("collapseSide").addEventListener("click", () => setSide(false));
$("showSide").addEventListener("click", () => setSide(true));
$("scrim").addEventListener("click", () => setSide(false));
if (store.get("eka-side", true) === false) app.classList.add("side-closed");
const closeDrawer = () => { if (phone()) app.classList.remove("drawer"); };

// ── routing: #/ (new), #/c/<id>, #/library ───────────────────────────────
function route() {
  const hash = location.hash || "#/";
  closeDrawer();
  if (hash.startsWith("#/library")) return showView("lib");
  showView("chat");
  const m = hash.match(/^#\/c\/([0-9a-f]{6,32})$/);
  if (m) { if (m[1] !== S.cid) openConversation(m[1]); }
  else if (S.cid) resetChat();
}
addEventListener("hashchange", route);
function showView(which) {
  $("viewChat").hidden = which !== "chat";
  $("viewLib").hidden = which !== "lib";
  $("navChat").classList.toggle("on", which === "chat");
  $("navLib").classList.toggle("on", which === "lib");
  $("scopeBtn").hidden = which !== "chat";
  $("titleBtn").hidden = which !== "chat" || !S.cid;
  $("title").textContent = which === "lib" ? "Document library" : (currentTitle() || "New chat");
  if (which === "lib") renderLibrary();
  else setTimeout(() => !phone() && $("q").focus({ preventScroll: true }), 0);
}
const currentTitle = () => (S.convs.find((c) => c.id === S.cid) || {}).title;

// ── conversation history ─────────────────────────────────────────────────
async function loadConversations() {
  try {
    const q = $("histQ").value.trim();
    S.convs = (await api("/api/conversations" + (q ? "?q=" + encodeURIComponent(q) : ""))).conversations;
  } catch (e) {
    $("hist").replaceChildren(h("div", "hist-empty", e.message));
    return;
  }
  renderHistory();
}
function dayGroup(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((start(now) - start(d)) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  if (days < 30) return "Previous 30 days";
  return d.toLocaleDateString([], { month: "long", year: "numeric" });
}
function renderHistory() {
  const box = $("hist");
  box.replaceChildren();
  $("titleBtn").hidden = !S.cid || !$("viewLib").hidden;
  if (!S.convs.length) {
    box.append(h("div", "hist-empty", $("histQ").value.trim() ? "No conversations match." : "Your conversations will appear here."));
    return;
  }
  let group = null;
  for (const c of S.convs) {
    const g = dayGroup(c.updated_at);
    if (g !== group) { box.append(h("div", "nav-g", g)); group = g; }
    const row = h("div", "hist-row");
    const a = h("a", "nav-i" + (c.id === S.cid ? " on" : ""));
    a.href = "#/c/" + c.id;
    a.append(h("span", "t", c.title));
    if (c.id === S.cid) a.setAttribute("aria-current", "page");
    const more = btn("hist-more", "", "more", "Conversation options");
    more.setAttribute("aria-haspopup", "true");
    more.addEventListener("click", (e) => { e.preventDefault(); convMenu(more, c, a); });
    row.append(a, more);
    box.append(row);
  }
}
function convMenu(owner, conv, link) {
  openPop(owner, (pop) => {
    const rename = btn("mi", "Rename", "edit");
    rename.addEventListener("click", () => { closePop(); renameInline(conv, link); });
    const del = btn("mi danger", "Delete", "trash");
    del.addEventListener("click", async () => {
      closePop();
      if (!confirm(`Delete "${conv.title}"? This removes the conversation and its answers.`)) return;
      try {
        await api("/api/conversations/" + conv.id, { method: "DELETE" });
        if (conv.id === S.cid) location.hash = "#/";
        toast("Conversation deleted");
        loadConversations();
      } catch (e) { toast(e.message); }
    });
    pop.append(rename, del);
  }, "right");
}
// the conversation's title in the top bar opens the same menu as its row in the sidebar
$("titleBtn").addEventListener("click", () => {
  const conv = S.convs.find((c) => c.id === S.cid);
  if (conv) convMenu($("titleBtn"), conv, $("title"));
});
$("title").addEventListener("click", () => { if (!$("titleBtn").hidden) $("titleBtn").click(); });
function renameInline(conv, link) {
  const input = h("input", "in");
  input.value = conv.title;
  input.setAttribute("aria-label", "Conversation title");
  link.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const title = input.value.trim();
    if (save && title && title !== conv.title) {
      try { await api("/api/conversations/" + conv.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) }); }
      catch (e) { toast(e.message); }
    }
    await loadConversations();
    if (input.isConnected) input.replaceWith(link);    // the top bar keeps its own title element
    if (conv.id === S.cid) $("title").textContent = currentTitle() || "New chat";
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}
let histTimer;
$("histQ").addEventListener("input", () => { clearTimeout(histTimer); histTimer = setTimeout(loadConversations, 200); });

async function openConversation(id) {
  S.cid = id;
  clearLog();
  setEmpty(false);
  const wait = h("div", "ch-done", "Loading conversation…");
  $("log").append(wait);
  try {
    const data = await api("/api/conversations/" + id);
    wait.remove();
    $("title").textContent = data.conversation.title;
    data.turns.forEach((t) => {
      addUser(t.question, t.created_at);
      const node = h("div", "msg bot");
      $("log").append(node);
      renderAnswer(node, t.answer, { feedback: t.feedback, created: t.created_at });
    });
    setEmpty(!data.turns.length);
    S.scope = new Set(store.get("eka-scope-" + id, []));
    renderScope();
    requestAnimationFrame(() => toEnd(false, true));
  } catch (e) {
    wait.textContent = e.message;
  }
  renderHistory();
}
function resetChat() {
  if (S.busy && S.abort) S.abort.abort();
  S.cid = null;
  S.scope = new Set();
  renderScope();
  clearLog();
  setEmpty(true);
  $("title").textContent = "New chat";
  renderHistory();
  loadSuggestions();
}
function newChat() {
  closeDrawer();
  if (location.hash !== "#/") history.pushState(null, "", "#/");
  showView("chat");
  resetChat();
  $("q").focus();
}
$("newChat").addEventListener("click", newChat);
$("newChatTop").addEventListener("click", newChat);
$("brandLink").addEventListener("click", (e) => { e.preventDefault(); newChat(); });

// ── conversation log and scrolling (the ATLAS follow / "Latest" behaviour) ─
const log = $("log");
let userScrollAt = 0;   // last time the reader scrolled the conversation themselves
const nearEnd = () => log.scrollHeight - log.scrollTop - log.clientHeight < 90;
function toEnd(smooth, force) {
  if (!force && !S.followScroll) { $("jump").hidden = false; return; }
  log.scrollTo({ top: log.scrollHeight, behavior: smooth && !reduced() ? "smooth" : "auto" });
}
log.addEventListener("scroll", () => {
  S.followScroll = nearEnd();
  if (S.followScroll) $("jump").hidden = true;
  $("main").classList.toggle("scrolled", log.scrollTop > 4);
}, { passive: true });
$("jump").addEventListener("click", () => { S.followScroll = true; $("jump").hidden = true; toEnd(true, true); });
// ATLAS: a long answer is read from its start, so the question goes to the top —
// unless the reader scrolled away while waiting, in which case "Latest" appears.
let placeSeq = 0;
function showFrom(el, since) {
  const seq = ++placeSeq;          // a newer question cancels this placement
  if (userScrollAt > since) { S.followScroll = nearEnd(); if (!S.followScroll) $("jump").hidden = false; return; }
  const place = () => {
    if (seq !== placeSeq || userScrollAt > since || !el.isConnected) return;
    const top = el.offsetTop - log.offsetTop - 14;
    log.scrollTop = log.scrollHeight - top > log.clientHeight ? top : log.scrollHeight;
    S.followScroll = nearEnd();
    $("jump").hidden = true;
  };
  place();
  // the answer's entrance animation and late layout (fonts, tables) settle over a few frames
  requestAnimationFrame(place);
  setTimeout(place, 120);
  setTimeout(place, 480);
}
function clearLog() { placeSeq++; log.replaceChildren(); $("jump").hidden = true; S.followScroll = true; }
function setEmpty(on) { app.classList.toggle("is-empty", on); }
function addUser(text, created, context) {
  const m = h("div", "msg me");
  const b = h("div", "bub");
  rich(text, b);
  // what the question was limited to, as the reader chose it
  if (context && context.length) {
    const c = h("div", "me-ctx");
    context.forEach((t) => { const k = h("span", "me-k"); k.append(icon("page"), docName(t)); c.append(k); });
    m.append(c);
  }
  m.append(b);
  if (created) m.dataset.created = created;
  log.append(m);
  return m;
}
function avatar() {
  return staticMascot(32, "m-av");     // beside finished answers Luma is a still image
}
// Luma's state follows the real request lifecycle: each server stage names the pipeline
// step that has just started (eka/answer.py, PHASES). Nothing is simulated on a timer.
const PHASE_STATE = {
  understanding: "thinking", searching: "searching", reading: "reading",
  analyzing: "analyzing", writing: "writing", checking: "analyzing",
};

// ── the activity trail: the request's real steps, as the server reported them ─
const PHASE_ICON = { waiting: "up", understanding: "spark", searching: "search", reading: "page",
  analyzing: "chart", writing: "edit", checking: "shield" };
const fmtDur = (ms) => (ms < 1000 ? Math.max(1, Math.round(ms)) + " ms" : (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + " s");
// a document's name shortens from its end, but its revision always stays readable (Rev A vs Rev B)
function docName(title, cls) {
  const [, name, rev] = title.match(/^(.*?)( Rev \S+)?$/);
  const s = h("span", cls);
  s.append(h("span", "dn", name));
  if (rev) s.append(h("span", "dr", rev));
  return s;
}
function docChip(title) {
  const c = h("span", "tr-doc");
  c.append(icon("page"), docName(title));
  return c;
}
function stepRow(step, dur) {
  const row = h("div", "tr-s");
  row.dataset.phase = step.phase;
  const ic = h("span", "tr-i");
  ic.append(icon(PHASE_ICON[step.phase] || "spark"), h("i", "tr-spin"));
  row.append(ic, h("span", "tr-t", step.text));
  if (step.docs && step.docs.length) {
    const d = h("span", "tr-docs");
    step.docs.forEach((t) => d.append(docChip(t)));
    row.append(d);
  }
  if (dur !== undefined) row.append(h("span", "tr-d", fmtDur(dur)));
  return row;
}
class Trail {
  constructor() { this.el = h("div", "trail"); this.steps = []; }
  add(text, phase, docs) {
    const now = performance.now();
    // "Sending" is the browser waiting for the server, not a step of the answer: it goes once one starts
    if (this.steps.length === 1 && this.steps[0].phase === "waiting") { this.steps = []; this.el.replaceChildren(); }
    const prev = this.el.querySelector(".tr-s.now");
    if (prev) { prev.classList.remove("now"); prev.querySelector(".th-label")?.classList.remove("th-label"); }
    const step = { text, phase, docs: docs || [], at: now };
    this.steps.push(step);
    const row = stepRow(step);
    row.classList.add("now");
    row.querySelector(".tr-t").classList.add("th-label");
    this.el.append(row);
  }
  // each step lasted until the next began; the last until the answer arrived
  timed(end) {
    return this.steps.filter((s) => s.phase !== "waiting").map((s, i, all) => ({ ...s, ms: (all[i + 1] ? all[i + 1].at : end) - s.at }));
  }
}

// ── answer rendering ─────────────────────────────────────────────────────
const STATUS = {
  needs_clarification: ["ask", "Needs clarification"],
  insufficient_evidence: ["none", "Not in your documents"],
  conflict: ["conflict", "Sources disagree"],
  error: ["conflict", "Something went wrong"],
};
const shortTitle = (c) => c.title || c.filename || "Document";
function sourcePill(c, n, compact) {
  const p = h("button", "src cite" + (compact ? " compact" : ""));
  p.type = "button";
  p.append(h("b", "", String(n)));
  const nm = h("span", "nm", shortTitle(c) + (c.revision ? " Rev " + c.revision : ""));
  p.append(nm, document.createTextNode("p. " + c.page));
  p.title = `Open ${c.title}${c.revision ? " Rev " + c.revision : ""}, page ${c.page}`;
  p.setAttribute("aria-label", p.title);
  p.addEventListener("click", () => openCitation(c, p));
  return p;
}
function renderAnswer(node, a, meta = {}) {
  node.className = "msg bot";
  // a live answer keeps the mascot that worked on it: it reacts once, then rests as a still
  const live = meta.mascot;
  node.replaceChildren(live ? live.size(32).el : avatar());
  if (live) live.el.classList.add("m-av");
  const bub = h("div", "bub");
  node.append(bub);
  node.dataset.messageId = a.message_id || "";
  node.dataset.mode = a.mode || "";
  const cites = Object.fromEntries((a.citations || []).map((c, i) => [c.id, { ...c, n: i + 1 }]));
  const pills = (ids, compact) => (ids || []).filter((id) => cites[id]).map((id) => sourcePill(cites[id], cites[id].n, compact));

  // how the answer was made comes first, folded into one line (the steps it took, what it searched)
  const how = trailSummary(a, meta.steps, meta.elapsed);
  if (how) bub.append(how);
  const st = STATUS[a.status];
  if (st) {
    const line = h("div", "st " + st[0]);
    line.append(h("i"), document.createTextNode(st[1]));
    bub.append(line);
  }
  if (a.status === "error") {
    const e = h("div", "err-b", a.message);
    bub.append(e);
  } else if (a.message) {
    bub.append(md(a.message));
  }

  if (a.clarification) {
    const q = h("p");
    q.append(h("strong", "", a.clarification.question));
    bub.append(q);
    const opts = h("div", "opts");
    (a.clarification.options || []).forEach((o) => {
      const label = typeof o === "string" ? o : o.label;
      const b = h("button", "dl");
      b.type = "button";
      b.append(document.createTextNode(label));
      if (o.title && o.title !== label) b.append(h("small", "", o.title));
      b.addEventListener("click", () => {
        if (S.busy) return;
        opts.querySelectorAll(".dl").forEach((x) => { x.disabled = true; });
        b.classList.add("picked");
        ask(label, { docIds: o.doc_id ? [o.doc_id] : null });
      });
      opts.append(b);
    });
    if (opts.childElementCount) bub.append(opts);
  }

  // a figure that was asked for comes first; text explains it underneath
  if (a.mode === "visual" && a.figures && a.figures.length) bub.append(renderFigures(a.figures, cites));
  // what changed between revisions is the table; the statements and new sections follow it
  const tableFirst = a.mode === "revision_diff";
  if (tableFirst && a.table && a.table.rows && a.table.rows.length) bub.append(renderTable(a.table, pills));
  renderClaims(bub, a.claims || [], pills, !a.message);
  if (a.mode !== "visual" && a.figures && a.figures.length) bub.append(renderFigures(a.figures, cites));
  if (!tableFirst && a.table && a.table.rows && a.table.rows.length) bub.append(renderTable(a.table, pills));
  if (a.visual) {
    const v = renderVisual(a.visual);
    if (v) bub.append(v);
  }

  (a.conflicts || []).forEach((c) => {
    const box = h("div", "rev");
    box.append(h("b", "", c.kind === "revision" ? "Older revision differs. " : "Conflict. "), document.createTextNode(c.summary));
    if (c.claims && c.claims.length) {
      const inner = h("div");
      renderClaims(inner, c.claims, pills, false);
      box.append(inner);
    }
    bub.append(box);
  });
  if (a.unresolved && a.unresolved.length) {
    const ul = h("ul", "notes");
    a.unresolved.forEach((u) => ul.append(h("li", "", u)));
    bub.append(ul);
  }
  if (a.mode_note) {
    const n = h("div", "mnote");
    n.append(icon("spark"), h("span", "", a.mode_note));
    bub.append(n);
  }
  if (a.safety_notice) {
    const s = h("div", "safety");
    s.append(icon("shield"), h("span", "", a.safety_notice));
    bub.append(s);
  }

  if (a.citations && a.citations.length) bub.append(sourceList(a.citations));
  if (a.status !== "error" || a.message_id) bub.append(actions(node, a, meta));
  if (a.follow_ups && a.follow_ups.length) bub.append(renderFollowUps(a.follow_ups));
  [...bub.children].forEach((el, i) => el.style.setProperty("--k", Math.min(i, 8)));
  // only now, with the whole answer on screen, does Luma react: a small bounce for an answer,
  // concern for an error, and simply rest for a clarification or a "not found"
  if (live) live.set(a.status === "error" ? "error" : a.status === "answered" ? "done" : "static");
}
function renderClaims(into, claims, pills, leadFirst) {
  let i = 0;
  let lead = leadFirst;
  while (i < claims.length) {
    const c = claims[i];
    if (c.kind === "outline") {
      const ul = h("ul", "outline");
      while (i < claims.length && claims[i].kind === "outline") {
        const o = claims[i++];
        const li = h("li");
        if (o.heading) li.append(h("b", "", o.heading));
        const t = h("span");
        rich(o.text, t);
        pills(o.citations).forEach((p) => t.append(p));
        li.append(t);
        ul.append(li);
      }
      into.append(ul);
      continue;
    }
    if (c.kind === "general") {
      const box = h("div", "gk");
      box.append(h("div", "gk-h", "General engineering knowledge · not from your documents"));
      while (i < claims.length && claims[i].kind === "general") box.append(rich(claims[i++].text, h("p")));
      into.append(box);
      continue;
    }
    if (c.kind === "location") {
      const row = h("div", "loc");
      row.append(h("span", "", c.text));
      pills(c.citations).forEach((p) => { p.style.marginLeft = "0"; row.append(p); });
      into.append(row);
      i++;
      continue;
    }
    if (c.source_kind === "table_row" && c.cells && !c.quote) {
      // consecutive raw rows of one table become one specification table
      const rows = [c];
      while (i + 1 < claims.length && claims[i + 1].cells && !claims[i + 1].quote && claims[i + 1].caption === c.caption) rows.push(claims[++i]);
      const cols = [];
      rows.forEach((r) => r.cells.forEach(([k]) => { if (!cols.includes(k)) cols.push(k); }));
      const spec = h("div", "spec");
      spec.append(h("div", "spec-h", c.caption || "Specification"));
      const wrap = h("div", "tbl"), t = h("table"), tr = t.createTHead().insertRow();
      cols.forEach((k) => tr.append(h("th", "", k || "")));
      tr.append(h("th", "", "Source"));
      const body = t.createTBody();
      rows.forEach((r) => {
        const row = body.insertRow();
        const map = Object.fromEntries(r.cells.map(([k, v]) => [k, v]));
        cols.forEach((k, ci) => {
          const td = row.insertCell();
          td.dataset.k = k;
          if (ci > 0) td.className = "v";
          rich(map[k] || "", td);
        });
        const s = row.insertCell();
        s.dataset.k = "Source";
        s.className = "s";
        pills(r.citations, true).forEach((p) => { p.style.marginLeft = "0"; s.append(p); });
      });
      wrap.append(t);
      spec.append(wrap);
      into.append(spec);
      i++;
      continue;
    }
    if (c.kind === "warning") {
      const w = h("div", "warn");
      const body = h("div");
      rich(c.text, body);
      pills(c.citations).forEach((p) => body.append(p));
      w.append(icon("warn"), body);
      into.append(w);
    } else if (c.kind === "inference") {
      const row = h("div", "inf");
      const t = h("div");
      t.append(inline(c.text));
      pills(c.citations).forEach((p) => t.append(p));
      row.append(h("span", "cl-k", "Inference"), t);
      into.append(row);
    } else {
      const lines = c.text.split("\n");
      const isStep = (l) => /^\s*\d{1,2}[.)]\s/.test(l);
      const steps = lines.filter(isStep);
      if (steps.length >= 2) {
        // a documented procedure: keep the document's order and every line of it
        const q = h("div", "q");
        if (c.label) q.append(h("span", "tag-k" + (c.kind === "superseded" ? " old" : " new"), c.label));
        const first = lines.findIndex(isStep), last = lines.length - 1 - [...lines].reverse().findIndex(isStep);
        const keep = (l) => l.trim() && !/^\(continued\)$/.test(l.trim());
        const pre = lines.slice(0, first).filter(keep), post = lines.slice(last + 1).filter(keep);
        const ol = h("ol");
        steps.forEach((s) => ol.append(rich(s.replace(/^\s*\d{1,2}[.)]\s+/, ""), h("li"))));
        if (pre.length) q.append(rich(pre.join("\n"), h("div", "q-t")));
        q.append(ol);
        if (post.length) q.append(rich(post.join("\n"), h("div", "q-t")));
        const end = h("div");
        pills(c.citations).forEach((p) => { p.style.marginLeft = "0"; end.append(p); });
        q.append(end);
        into.append(q);
      } else {
        // a direct answer: Luma's sentences; consecutive sentences from the same source share one citation
        const para = h("p", "ans" + (lead ? " lead" : ""));
        if (c.kind === "superseded") para.append(h("span", "tag-k old", c.label || "Older revision"));
        else if (c.label) para.append(h("span", "tag-k new", c.label));
        const key = (x) => (x.citations || []).join(",");
        const same = (x) => x && x.kind === c.kind && !x.label && !x.source_kind?.startsWith("warn") && key(x) === key(c)
          && !/^\s*\d{1,2}[.)]\s/m.test(x.text) && ["direct", "superseded"].includes(x.kind);
        const group = [c];
        while (!c.label && same(claims[i + 1])) group.push(claims[++i]);
        group.forEach((g, gi) => {
          if (gi) para.append(document.createTextNode(" "));
          const span = rich(g.text, h("span"));
          if (g.quote && g.quote !== g.text) span.title = "On the page: " + g.quote;
          para.append(span);
        });
        pills(c.citations).forEach((p) => para.append(p));
        into.append(para);
        lead = false;
      }
    }
    i++;
  }
}
function renderTable(t, pills) {
  const box = h("div", "dt");
  const wrap = h("div", "tbl"), table = h("table"), tr = table.createTHead().insertRow();
  t.columns.forEach((k) => tr.append(h("th", "", k)));
  const body = table.createTBody();
  let group = null;
  t.rows.forEach((r) => {
    if (r.group && r.group !== group) {
      group = r.group;
      const g = body.insertRow();
      g.className = "dt-g";
      const td = g.insertCell();
      td.colSpan = t.columns.length;
      td.textContent = group;
    }
    const row = body.insertRow();
    r.cells.forEach((v, ci) => {
      const td = row.insertCell();
      td.dataset.k = t.columns[ci];
      if (ci > 0 && /\d/.test(v)) td.className = "v";
      if (v === "—") td.className = "dim";
      rich(v, td);
    });
    const s = row.insertCell();
    s.dataset.k = t.columns[t.columns.length - 1];
    s.className = "s";
    pills(r.citations, true).forEach((p) => { p.style.marginLeft = "0"; p.style.marginRight = "4px"; s.append(p); });
  });
  wrap.append(table);
  box.append(wrap);
  return box;
}
function renderFigures(figures, cites) {
  const box = h("div", "figs");
  figures.forEach((f) => {
    const fig = h("figure", "fig");
    const open = h("button", "fig-i");
    open.type = "button";
    open.title = `Open page ${f.page} of ${f.document || "the document"}`;
    const img = h("img");
    img.src = authed(f.image_url);
    img.alt = f.caption;
    img.loading = "lazy";
    open.append(img);
    const cite = cites[(f.citations || [])[0]];
    if (cite) open.addEventListener("click", () => openCitation(cite, open));
    const cap = h("figcaption");
    const badge = h("span", "badge");
    badge.append(icon("image"), document.createTextNode("Original figure"));
    cap.append(h("b", "", f.caption), badge);
    if (cite) cap.append(sourcePill(cite, cite.n, false));
    fig.append(open, cap);
    box.append(fig);
  });
  return box;
}
function renderVisual(v) {
  const box = h("div", "viz");
  const head = h("div", "viz-h");
  const badge = h("span", "badge concept");
  badge.append(icon("spark"), document.createTextNode("Conceptual"));
  if (v.kind === "bars" && v.items && v.items.length) {
    head.append(h("b", "", v.title + (v.unit ? ` (${v.unit})` : "")), badge);
    box.append(head);
    const max = Math.max(...v.items.map((x) => x.value)) || 1;
    const bars = h("div", "bars");
    bars.setAttribute("role", "img");
    bars.setAttribute("aria-label", v.title + ": " + v.items.map((x) => `${x.name} ${x.text}`).join(", "));
    const colors = ["--chart-1", "--chart-2", "--chart-3", "--chart-4"];
    v.items.forEach((x, i) => {
      const row = h("div", "bar");
      const track = h("span", "bar-t"), fill = h("i");
      fill.style.width = Math.max(2, (x.value / max) * 100) + "%";
      fill.style.setProperty("--bar", `var(${colors[i % colors.length]})`);
      track.append(fill);
      row.append(h("span", "bar-n", x.name), track, h("span", "bar-v", x.text));
      bars.append(row);
    });
    box.append(bars);
  } else if (v.kind === "flow" && v.steps && v.steps.length) {
    head.append(h("b", "", "Procedure at a glance"), badge);
    box.append(head);
    const ol = h("ol", "flow");
    v.steps.forEach((s) => {
      const li = h("li"), chip = h("span"), t = h("em", "", s.length > 44 ? s.slice(0, 42).replace(/\s+\S*$/, "") + "…" : s);
      chip.title = s;
      chip.append(t);
      li.append(chip);
      ol.append(li);
    });
    box.append(ol);
  } else {
    return null;
  }
  box.append(h("div", "viz-n", v.label));
  return box;
}
function renderFollowUps(items) {
  const box = h("div", "fu");
  box.setAttribute("aria-label", "Suggested follow-ups");
  items.forEach((text) => {
    const b = btn("", text, "spark");
    b.addEventListener("click", () => ask(text));
    box.append(b);
  });
  return box;
}
// The sources an answer cites, numbered as its citation chips are. Each opens its page.
function sourceList(cs) {
  const box = h("div", "res");
  box.append(h("div", "res-h", `${cs.length} source${cs.length > 1 ? "s" : ""}`));
  const ul = h("ul", "srcs");
  cs.forEach((c, i) => {
    const li = h("li", "sc");
    const open = h("button", "sc-o");
    open.type = "button";
    open.append(h("span", "sc-n", String(i + 1)), icon("page"),
      docName(`${c.title}${c.revision ? " Rev " + c.revision : ""}`, "sc-t"), h("span", "sc-p", `p. ${c.page}`));
    if (c.ocr) open.append(h("span", "tag y", "OCR"));
    if (c.superseded) open.append(h("span", "tag y", "Superseded"));
    open.title = `View page ${c.page}${c.section ? " · " + c.section : ""}`;
    open.addEventListener("click", () => openCitation(c, open));
    const pdf = h("a", "sc-pdf");
    pdf.href = authed(c.open_url);
    pdf.target = "_blank";
    pdf.rel = "noopener";
    pdf.title = "Open the PDF";
    pdf.setAttribute("aria-label", `Open ${c.title} (PDF)`);
    pdf.append(icon("ext"));
    const ex = h("p", "sc-x");
    rich("“" + c.excerpt + "”", ex);
    li.append(open, pdf, ex);
    ul.append(li);
  });
  box.append(ul);
  if (cs.length > 3) {
    box.classList.add("clip");
    const more = btn("res-more", `Show all ${cs.length}`, "chevd");
    more.addEventListener("click", () => { box.classList.remove("clip"); more.remove(); });
    box.append(more);
  }
  return box;
}
// One line above the answer: what was searched and how long it took. Opened, it lists the steps
// as they happened (live answers) and the retrieval details (every answer).
function trailSummary(a, steps, elapsed) {
  const searched = a.intent === "question" && a.searched && a.searched.documents !== undefined;
  steps = steps || [];
  if (!searched && steps.length < 2) return null;
  const ev = h("details", "trail-x how");
  const sum = h("summary");
  const parts = [];
  if (searched) {
    const n = a.searched.documents, used = (a.documents_used || []).length;
    parts.push(`Searched ${n} document${n === 1 ? "" : "s"}`);
    if (used) parts.push(`used ${used}`);
  } else {
    parts.push(`${steps.length} steps`);
  }
  const ms = elapsed !== undefined ? elapsed : a.latency_ms;
  if (ms !== undefined && ms !== null) parts.push(fmtDur(ms));
  sum.append(icon("spark"), h("span", "", parts.join(" · ")), icon("chev"));
  ev.append(sum);
  const b = h("div", "evx-b");
  if (steps.length) {
    const list = h("div", "trail done");
    steps.forEach((s) => list.append(stepRow(s, s.ms)));
    b.append(list);
  }
  if (searched) howBody(a, b);
  ev.append(b);
  return ev;
}
function howBody(a, b) {
  const used = (a.documents_used || []).length;
  b.append(h("p", "", `Searched ${a.searched.documents} document${a.searched.documents === 1 ? "" : "s"} (${a.searched.scope}).`));
  if (used) b.append(h("p", "", "Used: " + a.documents_used.map((d) => `${d.title}${d.revision ? " Rev " + d.revision : ""} (p. ${d.pages.join(", ")})`).join("; ") + "."));
  if (a.searched.missing_identifiers && a.searched.missing_identifiers.length)
    b.append(h("p", "", "Not in any searched document: " + a.searched.missing_identifiers.join(", ") + "."));
  const method = a.generator === "extractive" ? "quoted from the documents (no language model)" : a.generator;
  b.append(h("p", "", `Answer method: ${method}.` + (a.fallback_reason ? ` The model was not used: ${a.fallback_reason}.` : "")));
  const rows = [...((a.trace || {}).selected || []), ...((a.trace || {}).rejected || [])];
  if (rows.length) {
    const titles = Object.fromEntries(S.docs.map((d) => [d.id, docTitle(d)]));
    const w = h("div", "trace-w"), t = h("table", "trace"), tr = t.createTHead().insertRow();
    const simLabel = S.health && S.health.semantic_search ? "Semantic" : "Char. overlap";
    ["Passage", "Page", "BM25", simLabel, "Coverage", "Decision"].forEach((x) => tr.append(h("th", "", x)));
    const body = t.createTBody();
    rows.forEach((r) => {
      const row = body.insertRow();
      row.insertCell().textContent = titles[r.doc_id] || r.doc_id;
      [r.page, r.bm25, r.cosine, r.coverage].forEach((v) => { const td = row.insertCell(); td.className = "n"; td.textContent = v; });
      row.insertCell().textContent = r.decision;
    });
    w.append(t);
    b.append(w);
  }
}
function actions(node, a, meta) {
  const fb = h("div", "fb");
  const t = h("time", "", fmtTime(meta.created || Date.now() / 1000));
  fb.append(t);
  const copy = btn("", "Copy", "copy", "Copy answer");
  copy.addEventListener("click", () => copyText(a.answer_text || a.message || ""));
  fb.append(copy);
  if (a.message_id) {
    const retry = btn("retry", "Retry", "retry", "Ask again");
    retry.addEventListener("click", () => {
      if (S.busy) return;
      const user = node.previousElementSibling;
      const question = a.question || (user && user.textContent) || "";
      node.remove();
      if (user && user.classList.contains("me")) user.remove();
      ask(question, { retryOf: a.message_id });
    });
    fb.append(retry);
    const up = btn("", "", "good", "Helpful"), down = btn("", "", "bad", "Not helpful");
    const set = (v) => {
      up.setAttribute("aria-pressed", String(v === "up"));
      down.setAttribute("aria-pressed", String(v === "down"));
    };
    set(meta.feedback || "");
    const send = async (v) => {
      const next = up.getAttribute("aria-pressed") === "true" && v === "up" || down.getAttribute("aria-pressed") === "true" && v === "down" ? "" : v;
      try {
        await api(`/api/messages/${a.message_id}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value: next }) });
        set(next);
        if (next) toast("Thanks for the feedback");
      } catch (e) { toast(e.message); }
    };
    up.addEventListener("click", () => send("up"));
    down.addEventListener("click", () => send("down"));
    fb.append(up, down);
  }
  // Retry is offered on the latest answer only, so history keeps its order.
  queueMicrotask(() => {
    log.querySelectorAll(".fb .retry").forEach((r) => { r.hidden = r.closest(".msg") !== log.querySelector(".msg.bot:last-of-type"); });
  });
  return fb;
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = h("textarea");
    ta.value = text;
    document.body.append(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  toast("Answer copied");
}

// ── asking: streamed over server-sent events, stoppable ──────────────────
const q = $("q"), send = $("send");
function syncComposer() {
  send.disabled = !S.busy && !q.value.trim();
  send.classList.toggle("stop", S.busy);
  send.replaceChildren(icon(S.busy ? "stop" : "up"));
  send.setAttribute("aria-label", S.busy ? "Stop" : "Send");
  send.title = S.busy ? "Stop generating" : "Send (Enter)";
}
function autosize() {
  if (CSS.supports("field-sizing", "content")) return;
  q.style.height = "auto";
  q.style.height = Math.min(q.scrollHeight, 220) + "px";
}
q.addEventListener("input", () => { autosize(); syncComposer(); });
q.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
});
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  if (S.busy) { stop(); return; }
  const text = q.value.trim();
  if (!text) return;
  q.value = "";
  autosize();
  ask(text);
});
function stop() { if (S.abort) S.abort.abort(); }

async function ask(text, opts = {}) {
  if (S.busy || !text.trim()) return;
  S.busy = true;
  syncComposer();
  setEmpty(false);
  S.followScroll = true;
  const scoped = opts.docIds || (S.scope.size ? [...S.scope] : null);
  const user = addUser(text, null, scoped && S.docs.filter((d) => scoped.includes(d.id)).map((d) => docTitle(d) + (d.revision ? " Rev " + d.revision : "")));
  const node = h("div", "msg bot think");
  // until the server's first stage arrives, the question is genuinely in flight
  const luma = new LumaMascot({ size: 44, state: "waiting", rest: "static", cls: "m-av" });
  node.dataset.luma = "live";
  const trail = new Trail();
  trail.add("Sending your question", "waiting");
  const th = h("div", "th");
  th.append(trail.el);
  node.append(luma.el, th);
  log.append(node);
  toEnd(true, true);
  const controller = new AbortController();
  S.abort = controller;
  const askedAt = performance.now();
  placeSeq++;
  const body = {
    message: text,
    conversation_id: S.cid,
    document_ids: scoped,
    retry_of: opts.retryOf || null,
  };
  let answer = null;
  try {
    const headers = { "Content-Type": "application/json", "X-EKA-Client": clientId() };
    if (S.token) headers.Authorization = "Bearer " + S.token;
    const res = await fetch("/api/chat/stream", { method: "POST", headers, body: JSON.stringify(body), signal: controller.signal });
    if (res.status === 401 && askToken()) { S.busy = false; user.remove(); node.remove(); return ask(text, opts); }
    if (!res.ok || !res.body) {
      let detail = `HTTP ${res.status}`;
      try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
      throw new Error(typeof detail === "string" ? detail : "The request was rejected.");
    }
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let cut;
      while ((cut = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        const ev = (raw.match(/^event: (.*)$/m) || [])[1];
        const data = (raw.match(/^data: (.*)$/m) || [])[1];
        if (!ev || !data) continue;
        const payload = JSON.parse(data);
        if (ev === "stage") {
          trail.add(payload.text, payload.phase, payload.docs);
          if (PHASE_STATE[payload.phase]) luma.set(PHASE_STATE[payload.phase]);
        }
        else if (ev === "answer") answer = payload;
        else if (ev === "error") throw new Error(payload.detail);
      }
    }
    if (!answer) throw new Error("The connection closed before the answer arrived.");
    const isNew = !S.cid;
    S.cid = answer.conversation_id;
    if (S.scope.size) store.set("eka-scope-" + S.cid, [...S.scope]);
    const end = performance.now();
    renderAnswer(node, answer, { created: Date.now() / 1000, mascot: luma, steps: trail.timed(end), elapsed: end - askedAt });
    showFrom(user, askedAt);
    if (isNew) history.replaceState(null, "", "#/c/" + S.cid);
    await loadConversations();
    $("title").textContent = currentTitle() || "New chat";
  } catch (e) {
    if (e.name === "AbortError") {
      luma.destroy();
      node.replaceWith(h("div", "ch-done", "Stopped. This answer was not saved."));
    } else {
      node.className = "msg bot";
      node.replaceChildren(luma.size(32).el);
      luma.set("error");
      const b = h("div", "bub");
      const err = h("div", "err-b", e.message);
      const again = btn("cbtn sm", "Try again", "retry");
      again.addEventListener("click", () => { user.remove(); node.remove(); ask(text, opts); });
      err.append(again);
      b.append(err);
      node.append(b);
    }
  } finally {
    S.busy = false;
    S.abort = null;
    syncComposer();
    if (!answer) toEnd(false);
    // Return to the message box unless the user has already moved on (e.g. Ctrl+K).
    const at = document.activeElement;
    if (!phone() && (!at || at === document.body || at === send || at === q || node.contains(at))) q.focus({ preventScroll: true });
  }
}

// ── suggestions (grounded: the server only offers questions it can answer) ─
let sugSeq = 0;
async function loadSuggestions() {
  const box = $("sugs");
  const seq = ++sugSeq;           // only the latest request may render
  box.replaceChildren();
  const ready = S.docs.filter(searchable).length;
  if (!ready) {
    $("emptyP").textContent = S.docs.length
      ? "Your documents are still being processed. Questions can be asked as soon as one shows Ready."
      : "Upload engineering PDFs — manuals, datasheets, procedures — and ask about specifications, part numbers or warnings. Every answer cites its page.";
    const up = btn("ch-chip up", "Upload a PDF", "upload");
    up.addEventListener("click", () => $("attachFile").click());
    box.append(up);
    return;
  }
  $("emptyP").textContent = `Ask about specifications, procedures, part numbers or warnings in your ${ready} document${ready > 1 ? "s" : ""}. I can also compare equipment, summarize a manual and show its figures.`;
  try {
    const { suggestions } = await api("/api/suggestions?limit=4");
    if (seq !== sugSeq) return;
    box.replaceChildren();
    suggestions.forEach((s, i) => {
      const c = btn("ch-chip", s);
      c.style.setProperty("--i", i);
      c.addEventListener("click", () => ask(s));
      box.append(c);
    });
  } catch { /* suggestions are optional */ }
}

// ── documents: polling, library page, uploads, attachments ───────────────
async function loadDocs() {
  try {
    S.docs = (await api("/api/documents")).documents;
  } catch (e) {
    $("docs").replaceChildren(h("li", "empty", e.message));
    return;
  }
  const busy = S.docs.some((d) => d.status === "queued" || d.status === "processing");
  $("libCount").textContent = S.docs.length;
  $("libCount").className = "nav-c" + (busy ? " fin" : "");
  for (const id of [...S.scope]) if (!S.docs.some((d) => d.id === id && searchable(d))) S.scope.delete(id);
  renderScope();
  if (!$("viewLib").hidden) renderLibrary();
  updateAttachments();
  clearTimeout(S.pollTimer);
  if (busy) S.pollTimer = setTimeout(loadDocs, 1200);
  else {
    const sig = S.docs.map((d) => d.id + d.status).join();
    if (sig !== S.docSig) {            // only when the library actually changed
      S.docSig = sig;
      loadHealth();
      if (app.classList.contains("is-empty")) loadSuggestions();
    }
    // the PDF folder can add documents at any time: look again every 20 s
    S.pollTimer = setTimeout(loadDocs, 20000);
  }
}
const STATUS_PILL = { ready: ["run", "Ready"], partial: ["wait", "Partial"], failed: ["bad", "Failed"], processing: ["fin", "Processing"], queued: ["fin", "Queued"] };
const PAGE_LABEL = { ok: "Text", ocr: "OCR", ocr_low_conf: "OCR · low confidence", empty: "Blank", ocr_unavailable: "Not read: needs OCR", ocr_failed: "OCR failed", error: "Error" };
// ── the watched PDF folder (phase 1 source) ──────────────────────────────
function renderSources() {
  const src = ((S.health || {}).sources || [])[0];
  $("srcBox").hidden = !src;
  if (!src) return;
  $("srcLoc").textContent = src.location;
  const when = src.last_scan ? "checked " + fmtTime(src.last_scan) : "not checked yet";
  $("srcStat").textContent = src.error ? "" :
    `${src.pdfs} PDF${src.pdfs === 1 ? "" : "s"} in the folder · ${src.indexed} indexed` +
    (src.processing ? ` · ${src.processing} processing` : "") + (src.failed ? ` · ${src.failed} failed` : "") +
    (src.pending ? ` · ${src.pending} still being copied` : "") + ` · ${when}. New PDFs are picked up automatically.`;
  const notes = $("srcNotes");
  notes.replaceChildren();
  if (src.error) notes.append(h("li", "bad", src.error));
  (src.errors || []).slice(0, 5).forEach((e) => notes.append(h("li", "bad", `${e.name}: ${e.reason}`)));
  (src.skipped || []).slice(0, 5).forEach((e) => notes.append(h("li", "", `${e.name}: ${e.reason}`)));
}
$("srcScan").addEventListener("click", async () => {
  const b = $("srcScan");
  b.disabled = true;
  try {
    const { sources } = await api("/api/sources/scan", { method: "POST" });
    if (S.health) S.health.sources = sources;
    renderSources();
    const st = sources[0] || {};
    toast(st.error ? "Folder problem: " + st.error : `Folder checked: ${st.added || 0} new, ${st.updated || 0} updated`);
    loadDocs();
  } catch (e) { toast(e.message); }
  finally { b.disabled = false; }
});

function renderLibrary() {
  renderSources();
  const list = $("docs"), f = $("libQ").value.trim().toLowerCase();
  const open = new Set([...list.querySelectorAll(".doc-pages")].map((n) => n.closest(".doc").dataset.id));
  const byId = Object.fromEntries(S.docs.map((d) => [d.id, d]));
  const shown = S.docs.filter((d) => !f || [d.title, d.filename, d.equipment, d.doc_number].join(" ").toLowerCase().includes(f));
  list.replaceChildren();
  $("libEmpty").hidden = S.docs.length > 0;
  $("libCnt").textContent = S.docs.length ? `${shown.length} of ${S.docs.length}` : "";
  for (const d of shown) {
    const li = h("li", "doc" + (d.superseded_by ? " old" : ""));
    li.dataset.id = d.id;
    let ic;
    if (d.status === "queued" || d.status === "processing") {
      // Luma reads the PDF while it is genuinely being processed; one mascot per document, reused
      S.readers = S.readers || new Map();
      if (!S.readers.has(d.id)) S.readers.set(d.id, new LumaMascot({ size: 36, state: d.status === "queued" ? "waiting" : "reading", cls: "doc-ic" }));
      const reader = S.readers.get(d.id).set(d.status === "queued" ? "waiting" : "reading");
      ic = reader.el;
    } else {
      if (S.readers && S.readers.has(d.id)) { S.readers.get(d.id).destroy(); S.readers.delete(d.id); }
      ic = h("span", "doc-ic");
      ic.append(icon("page"));
    }
    const m = h("div", "doc-m");
    const t = h("div", "doc-t", docTitle(d));
    t.title = docTitle(d);
    const meta = [];
    if (d.revision) meta.push("Rev " + d.revision);
    if (d.page_count) meta.push(`${d.page_count} page${d.page_count > 1 ? "s" : ""}`);
    if (d.equipment) meta.push(d.equipment);
    if (d.superseded_by) meta.push("superseded by Rev " + ((byId[d.superseded_by] || {}).revision || "?"));
    if (d.status === "queued" || d.status === "processing") meta.splice(0, meta.length, d.stage || "Waiting to be processed");
    meta.push(d.source && d.source.startsWith("folder:") ? "PDF folder: " + d.source.slice(7) : d.filename);
    const s = h("div", "doc-s", meta.join(" · "));
    s.title = meta.join(" · ");
    m.append(t, s);
    const ps = d.page_status || {};
    if (d.error) m.append(h("div", "doc-e" + (d.status === "failed" ? "" : " warn"), d.error));
    else if (ps.empty || ps.ocr || ps.ocr_low_conf) {
      const notes = [];
      if (ps.ocr || ps.ocr_low_conf) notes.push(`${(ps.ocr || 0) + (ps.ocr_low_conf || 0)} page(s) read by OCR`);
      if (ps.empty) notes.push(`${ps.empty} blank page(s)`);
      m.append(h("div", "doc-s", notes.join(" · ")));
    }
    const r = h("div", "doc-r");
    const [cls, label] = STATUS_PILL[d.status] || ["", d.status];
    const pill = h("span", "pill " + cls);
    pill.append(h("span", "d"), document.createTextNode(label));
    r.append(pill);
    if (d.page_count) {
      const view = btn("cbtn sm", "Open", "page");
      view.addEventListener("click", () => openViewer({ docId: d.id, page: 1, title: docTitle(d), rev: d.revision, pageCount: d.page_count }, view));
      r.append(view);
    }
    const more = btn("ibtn sm", "", "more", "Document options");
    more.addEventListener("click", () => docMenu(more, d, li));
    r.append(more);
    li.append(ic, m, r);
    if (d.status === "queued" || d.status === "processing") {
      const bar = h("div", "doc-p"), fill = h("span");
      fill.style.width = (d.progress_total ? Math.round(100 * d.progress_done / d.progress_total) : 4) + "%";
      bar.append(fill);
      li.append(bar);
    }
    list.append(li);
    if (open.has(d.id)) showPages(d, li);
  }
}
function docMenu(owner, d, li) {
  openPop(owner, (pop) => {
    const pages = btn("mi", li.querySelector(".doc-pages") ? "Hide page status" : "Page status", "page");
    pages.addEventListener("click", () => { closePop(); const p = li.querySelector(".doc-pages"); p ? p.remove() : showPages(d, li); });
    const re = btn("mi", "Re-index", "retry");
    re.disabled = d.status === "queued" || d.status === "processing";
    re.addEventListener("click", async () => {
      closePop();
      try { await api(`/api/documents/${d.id}/reindex`, { method: "POST" }); toast("Re-indexing " + docTitle(d)); loadDocs(); }
      catch (e) { toast(e.message); }
    });
    const pdf = btn("mi", "Download original", "ext");
    pdf.addEventListener("click", () => { closePop(); open(authed(`/api/documents/${d.id}/file`), "_blank", "noopener"); });
    const del = btn("mi danger", "Delete", "trash");
    del.addEventListener("click", async () => {
      closePop();
      if (!confirm(`Delete "${docTitle(d)}" from the library? Its file and index entries are removed.`)) return;
      try { await api(`/api/documents/${d.id}`, { method: "DELETE" }); toast("Document deleted"); loadDocs(); }
      catch (e) { toast(e.message); }
    });
    pop.append(pages, re, pdf, h("hr"), del);
  }, "right");
}
async function showPages(d, li) {
  try {
    const data = await api(`/api/documents/${d.id}`);
    const box = h("div", "doc-pages");
    const t = h("table"), tr = t.createTHead().insertRow();
    ["Page", "Read as", "Note"].forEach((x) => tr.append(h("th", "", x)));
    const b = t.createTBody();
    data.pages.forEach((p) => {
      const row = b.insertRow();
      row.insertCell().textContent = p.page_no;
      row.insertCell().textContent = PAGE_LABEL[p.status] || p.status;
      const notes = [p.reason];
      if (p.images && p.status === "ok") notes.push(`${p.images} image(s), not interpreted`);
      row.insertCell().textContent = notes.filter(Boolean).join("; ");
    });
    box.append(data.pages.length ? t : h("p", "doc-s", "No pages could be read."));
    li.querySelector(".doc-pages")?.remove();
    li.append(box);
  } catch (e) { toast(e.message); }
}
$("libQ").addEventListener("input", renderLibrary);
$("uploadBtn").addEventListener("click", () => $("libFile").click());
$("libFile").addEventListener("change", (e) => { [...e.target.files].forEach((f) => upload(f)); e.target.value = ""; });
const drop = $("drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); [...e.dataTransfer.files].forEach((f) => upload(f)); });

function upload(file, chip) {
  const li = chip ? null : h("li");
  const say = (text, bad) => {
    if (li) { li.className = bad ? "bad" : ""; li.replaceChildren(h("span", "", `${file.name}: ${text}`)); }
    if (chip) chipState(chip, text, bad ? "bad" : "busy");
  };
  if (li) $("uploads").prepend(li);
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") { say("only PDF files are supported", true); return; }
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/documents?filename=" + encodeURIComponent(file.name));
  xhr.setRequestHeader("Content-Type", "application/pdf");
  if (S.token) xhr.setRequestHeader("Authorization", "Bearer " + S.token);
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round(100 * e.loaded / e.total);
    if (li) { li.replaceChildren(h("span", "", file.name)); const p = h("progress"); p.max = 100; p.value = pct; li.append(p, h("span", "", pct + "%")); }
    if (chip) chipState(chip, `Uploading ${pct}%`, "busy");
  };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
    if (xhr.status >= 400) { say(data.detail || `upload failed (HTTP ${xhr.status})`, true); return; }
    if (chip) { chip.docId = data.document.id; chip.duplicate = data.duplicate; }
    say(data.duplicate ? "already in the library" : "uploaded, processing…");
    if (li) setTimeout(() => li.remove(), 6000);
    loadDocs();
  };
  xhr.onerror = () => say("the server is not reachable", true);
  xhr.send(file);
}
// Attachments from the composer go to the library; the chip follows processing.
$("attach").addEventListener("click", () => $("attachFile").click());
$("attachFile").addEventListener("change", (e) => {
  [...e.target.files].forEach((f) => {
    const el = h("span", "att busy");
    const nm = h("span", "nm", f.name);
    nm.title = f.name;
    const st = h("span", "s", "Uploading…");
    const x = btn("x", "", "x", "Remove from view");
    el.append(icon("page"), nm, st, x);
    const chip = { el, st, name: f.name, docId: null };
    x.addEventListener("click", () => { el.remove(); S.attachments.delete(chip); });
    S.attachments.set(chip, chip);
    $("atts").append(el);
    upload(f, chip);
  });
  e.target.value = "";
});
function chipState(chip, text, kind) {
  chip.st.textContent = text;
  chip.el.className = "att " + kind;
}
function updateAttachments() {
  for (const chip of S.attachments.values()) {
    if (!chip.docId) continue;
    const d = S.docs.find((x) => x.id === chip.docId);
    if (!d) continue;
    if (d.status === "ready") chipState(chip, chip.duplicate ? "Already in library · ready" : "Ready to ask about", "ok");
    else if (d.status === "partial") chipState(chip, "Ready · some pages unreadable", "ok");
    else if (d.status === "failed") chipState(chip, "Failed: " + (d.error || "unreadable"), "bad");
    else chipState(chip, d.progress_total ? `Processing page ${d.progress_done} of ${d.progress_total}` : "Processing…", "busy");
  }
}

// ── which documents a question searches ──────────────────────────────────
function renderScope() {
  const ready = S.docs.filter(searchable);
  $("scopeTxt").textContent = !S.scope.size ? `All documents${ready.length ? " (" + ready.length + ")" : ""}`
    : `${S.scope.size} document${S.scope.size > 1 ? "s" : ""}`;     // which ones: the chips beside it
  // the documents a question is limited to sit in the composer, each removable
  const chips = $("ctxChips");
  chips.replaceChildren();
  S.docs.filter((d) => S.scope.has(d.id)).forEach((d) => {
    const c = h("span", "cb-chip");
    const x = btn("x", "", "x", "Search all documents again");
    x.title = "Remove";
    x.addEventListener("click", () => { S.scope.delete(d.id); saveScope(); $("q").focus(); });
    c.append(icon("page"), docName(docTitle(d) + (d.revision ? " Rev " + d.revision : ""), "nm"), x);
    chips.append(c);
  });
  chips.hidden = !chips.childElementCount;
}
$("scopeBtn").addEventListener("click", () => openPop($("scopeBtn"), (pop) => {
  pop.append(h("div", "ph", "Search in"));
  const ready = S.docs.filter(searchable);
  const all = h("label", "mi");
  const allBox = h("input");
  allBox.type = "checkbox";
  allBox.checked = !S.scope.size;
  all.append(allBox, h("span", "", "All documents"));
  pop.append(all);
  const scroll = h("div", "scroll");
  const boxes = [];
  ready.forEach((d) => {
    const l = h("label", "mi"), b = h("input");
    b.type = "checkbox";
    b.checked = S.scope.has(d.id);
    const t = h("span");
    t.append(document.createTextNode(docTitle(d)), h("small", "", [d.revision && "Rev " + d.revision, d.superseded_by && "superseded", d.equipment].filter(Boolean).join(" · ")));
    l.append(b, t);
    b.addEventListener("change", () => {
      b.checked ? S.scope.add(d.id) : S.scope.delete(d.id);
      allBox.checked = !S.scope.size;
      saveScope();
    });
    boxes.push(b);
    scroll.append(l);
  });
  allBox.addEventListener("change", () => { S.scope.clear(); boxes.forEach((b) => { b.checked = false; }); allBox.checked = true; saveScope(); });
  if (!ready.length) scroll.append(h("div", "ph", "No searchable documents yet"));
  pop.append(h("hr"), scroll);
}, "right"));
function saveScope() { renderScope(); if (S.cid) store.set("eka-scope-" + S.cid, [...S.scope]); }

// ── document panel ───────────────────────────────────────────────────────
// Opening or closing it keeps the conversation exactly where the reader was:
// the first visible message is used as an anchor across the width change.
function chatAnchor() {
  const top = log.getBoundingClientRect().top;
  const el = [...log.children].find((c) => c.getBoundingClientRect().bottom > top + 8);
  // "end" only when truly at the bottom; otherwise hold the exact message and offset
  return el ? { el, off: el.getBoundingClientRect().top - top, end: log.scrollHeight - log.scrollTop - log.clientHeight < 4 } : null;
}
function holdAnchor(a) {
  if (!a || !a.el.isConnected) return;
  if (a.end) { log.scrollTop = log.scrollHeight; return; }
  log.scrollTop += a.el.getBoundingClientRect().top - log.getBoundingClientRect().top - a.off;
}
function restoreAnchor(a) {
  if (!a) return;
  const fix = () => holdAnchor(a);
  // The panel's width animates, so text keeps reflowing for ~0.35s: hold the
  // anchor on every frame until the layout has settled.
  const until = performance.now() + 520;
  const step = () => { fix(); if (performance.now() < until) requestAnimationFrame(step); };
  step();
}
let autoCollapsed = false, opener = null;
// The reader's place in the conversation while a document is open. It moves
// only when the reader scrolls the conversation themselves.
let readAnchor = null;
["wheel", "touchmove", "keydown"].forEach((t) => log.addEventListener(t, () => { userScrollAt = performance.now(); }, { passive: true }));
log.addEventListener("scroll", () => {
  if (app.classList.contains("viewing") && performance.now() - userScrollAt < 800) readAnchor = chatAnchor();
}, { passive: true });
function openCitation(c, from) {
  const d = S.docs.find((x) => x.id === c.doc_id);
  openViewer({ docId: c.doc_id, page: c.page, chunkId: c.chunk_id, quote: c.excerpt, citedPage: c.page,
    title: c.title, rev: c.revision, section: c.section, pageCount: d ? d.page_count : c.page, superseded: c.superseded }, from);
}
function openViewer(v, from) {
  opener = from || null;
  const wasOpen = app.classList.contains("viewing");
  if (!wasOpen || !readAnchor) readAnchor = chatAnchor();
  const anchor = readAnchor;
  S.viewer = { zoom: S.viewer ? S.viewer.zoom : 1, ...v, citedPage: v.citedPage || null, hits: [], q: "" };
  $("vQ").value = "";
  $("vHits").hidden = true;
  if (!wasOpen && desktop() && !app.classList.contains("side-closed")) {
    // Not enough room for a comfortable conversation beside the document:
    // fold the sidebar while reading, and bring it back on close.
    const viewerW = parseFloat(app.style.getPropertyValue("--viewer-w")) || Math.min(560, innerWidth * 0.42);
    if (innerWidth - 268 - viewerW < 640) { app.classList.add("side-closed"); autoCollapsed = true; }
  }
  app.classList.add("viewing");
  $("viewer").setAttribute("aria-hidden", "false");
  renderViewer();
  restoreAnchor(anchor);
  setTimeout(() => (phone() ? $("vBack") : $("vClose")).focus({ preventScroll: true }), 60);
}
function closeViewer() {
  if (!app.classList.contains("viewing")) return;
  const anchor = readAnchor || chatAnchor();
  readAnchor = null;
  app.classList.remove("viewing");
  $("viewer").setAttribute("aria-hidden", "true");
  if (autoCollapsed) { app.classList.remove("side-closed"); autoCollapsed = false; }
  restoreAnchor(anchor);
  S.viewer = null;
  if (opener && opener.isConnected) opener.focus({ preventScroll: true });
}
$("vClose").addEventListener("click", closeViewer);
$("vBack").addEventListener("click", closeViewer);
function renderViewer() {
  const v = S.viewer;
  if (!v) return;
  $("vTitle").textContent = (v.title || "Document") + (v.rev ? " Rev " + v.rev : "");
  $("vSub").textContent = v.citedPage && v.page === v.citedPage ? `Cited on page ${v.citedPage}${v.section ? " · " + v.section : ""}` : (v.superseded ? "Superseded revision" : "");
  $("vPage").value = v.page;
  $("vOf").textContent = "/ " + (v.pageCount || "?");
  $("vPrev").disabled = v.page <= 1;
  $("vNext").disabled = !!v.pageCount && v.page >= v.pageCount;
  $("vZoom").textContent = Math.round(v.zoom * 100) + "%";
  $("vOpen").href = authed(`/api/documents/${v.docId}/file#page=${v.page}`);
  const cited = v.citedPage && v.page === v.citedPage;
  $("vQuote").hidden = !(cited && v.quote);
  $("vQuoteT").textContent = v.quote ? "“" + v.quote + "”" : "";
  const params = new URLSearchParams({ zoom: String(Math.min(3, 1.4 * v.zoom * Math.min(devicePixelRatio || 1, 2))) });
  if (cited && v.chunkId) params.set("chunk", v.chunkId);
  if (v.q) params.set("q", v.q);
  const img = $("vImg"), load = $("vLoad");
  load.hidden = false;
  load.textContent = "Loading page…";
  img.style.setProperty("--zoom", v.zoom);
  img.alt = `${v.title}, page ${v.page}`;
  img.onload = () => { load.hidden = true; };
  img.onerror = () => { load.textContent = "This page could not be rendered. Use “Open the original PDF”."; };
  img.src = authed(`/api/documents/${v.docId}/pages/${v.page}/image?${params}`);
  $("vHits").querySelectorAll(".v-hit").forEach((x) => x.classList.toggle("on", Number(x.dataset.page) === v.page));
}
function goPage(p) {
  const v = S.viewer;
  if (!v) return;
  const n = Math.max(1, Math.min(v.pageCount || p, p));
  if (n !== v.page) { v.page = n; renderViewer(); $("vPageBox").scrollTop = 0; }
  else $("vPage").value = v.page;
}
$("vPrev").addEventListener("click", () => goPage(S.viewer.page - 1));
$("vNext").addEventListener("click", () => goPage(S.viewer.page + 1));
$("vPage").addEventListener("change", () => goPage(parseInt($("vPage").value, 10) || 1));
$("vPage").addEventListener("keydown", (e) => { if (e.key === "Enter") goPage(parseInt($("vPage").value, 10) || 1); });
const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2];
function zoom(dir) {
  const v = S.viewer;
  const i = ZOOMS.indexOf(v.zoom);
  v.zoom = ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, (i < 0 ? 2 : i) + dir))];
  renderViewer();
}
$("vIn").addEventListener("click", () => zoom(1));
$("vOut").addEventListener("click", () => zoom(-1));
let vqTimer;
$("vQ").addEventListener("input", () => { clearTimeout(vqTimer); vqTimer = setTimeout(searchDoc, 300); });
$("vQ").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    const v = S.viewer;
    if (!v || !v.hits.length) { searchDoc(); return; }
    const next = v.hits.find((x) => x.page > v.page) || v.hits[0];
    goPage(next.page);
  }
  if (e.key === "Escape" && $("vQ").value) { e.stopPropagation(); $("vQ").value = ""; searchDoc(); }
});
async function searchDoc() {
  const v = S.viewer;
  if (!v) return;
  const text = $("vQ").value.trim();
  const box = $("vHits");
  v.q = text.length >= 2 ? text : "";
  if (!v.q) { box.hidden = true; v.hits = []; renderViewer(); return; }
  try {
    const data = await api(`/api/documents/${v.docId}/search?q=${encodeURIComponent(v.q)}`);
    v.hits = data.results;
    box.replaceChildren();
    if (!v.hits.length) box.append(h("div", "v-hits-none", `No match for “${v.q}” in this document.`));
    v.hits.forEach((r) => {
      const b = h("button", "v-hit");
      b.type = "button";
      b.dataset.page = r.page;
      b.append(h("b", "", `p. ${r.page}`), document.createTextNode(`${r.hits} match${r.hits > 1 ? "es" : ""}${r.ocr ? " (OCR text)" : ""} · …${r.snippet}…`));
      b.addEventListener("click", () => goPage(r.page));
      box.append(b);
    });
    box.hidden = false;
    if (v.hits.length && !v.hits.some((x) => x.page === v.page)) goPage(v.hits[0].page);
    else renderViewer();
  } catch (e) { box.replaceChildren(h("div", "v-hits-none", e.message)); box.hidden = false; }
}
// resizable on wide screens
(() => {
  const grip = $("grip");
  const saved = store.get("eka-viewer-w", 0);
  if (saved) app.style.setProperty("--viewer-w", saved + "px");
  const setW = (x) => {
    const sideW = app.classList.contains("side-closed") ? 0 : 268;
    const w = Math.max(360, Math.min(innerWidth - x, innerWidth - sideW - 480));
    app.style.setProperty("--viewer-w", w + "px");
    return w;
  };
  grip.addEventListener("pointerdown", (e) => {
    // Track on the window: the grip moves with the edge being dragged, so it
    // cannot be relied on to keep receiving the pointer.
    e.preventDefault();
    app.classList.add("resizing");
    const move = (ev) => { setW(ev.clientX); holdAnchor(readAnchor); };
    const up = (ev) => {
      store.set("eka-viewer-w", setW(ev.clientX));
      holdAnchor(readAnchor);
      app.classList.remove("resizing");
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      removeEventListener("pointercancel", up);
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
    addEventListener("pointercancel", up);
  });
  grip.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const cur = $("viewer").getBoundingClientRect().width;
    store.set("eka-viewer-w", setW(innerWidth - cur + (e.key === "ArrowLeft" ? -24 : 24)));
    holdAnchor(readAnchor);
  });
})();

// ── keyboard ─────────────────────────────────────────────────────────────
document.addEventListener("keydown", (e) => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.shiftKey && (e.key === "O" || e.key === "o")) { e.preventDefault(); newChat(); return; }
  if (mod && (e.key === "k" || e.key === "K")) { e.preventDefault(); setSide(true); $("histQ").focus(); return; }
  if (mod && e.key === ".") { e.preventDefault(); phone() ? setSide(!app.classList.contains("drawer")) : setSide(app.classList.contains("side-closed")); return; }
  if (e.key === "/" && !typing && !$("viewChat").hidden) { e.preventDefault(); q.focus(); return; }
  if (e.key === "Escape") {
    if (popEl) { closePop(); return; }
    if ($("settings").open) return;   // the dialog closes itself
    if (app.classList.contains("viewing")) { closeViewer(); return; }
    if (app.classList.contains("drawer")) { setSide(false); return; }
    if (S.busy) stop();
  }
});

// ── start ────────────────────────────────────────────────────────────────
syncComposer();
loadHealth();
loadDocs().then(() => { route(); if (!S.cid) loadSuggestions(); });
loadConversations();

// ── Luma identity and the sidebar upload entry ───────────────────────────
document.title = `${BRAND.product} · ${BRAND.tagline}`;
$("brandName").textContent = BRAND.product;
$("brandTag").textContent = BRAND.tagline;
$("brandLink").setAttribute("aria-label", `${BRAND.product}, new chat`);
$("q").placeholder = `Ask ${BRAND.product} anything`;
$("navUpload").addEventListener("click", () => {
  closeDrawer();
  ($("viewLib").hidden ? $("attachFile") : $("libFile")).click();
});

// ── Luma on the welcome screen ──────────────────────────────────────────
{
  const hero = new LumaMascot({ size: 88, state: "idle", rest: "idle", cls: "ch-e-luma" });
  $("emptyLuma").replaceWith(hero.el);
}
