// Engineering Knowledge Assistant — client. No framework; all document text is
// inserted with textContent, never as HTML.

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
};
const narrow = matchMedia("(max-width: 900px)");

const state = {
  token: sessionStorage.getItem("eka-token") || "",
  conversationId: sessionStorage.getItem("eka-conversation") || null,
  docs: [],
  selected: new Set(JSON.parse(sessionStorage.getItem("eka-selected") || "[]")),
  viewer: null,          // {docId, page, pageCount, title, chunkId, quote}
  pollTimer: null,
  busy: false,
};

// ── API ──────────────────────────────────────────────────────────────────
function withToken(url) {
  if (!state.token) return url;
  const [path, hash] = url.split("#");
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(state.token) + (hash ? "#" + hash : "");
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch {
    throw new Error("The server is not reachable. Check that it is running.");
  }
  if (response.status === 401) {
    const token = prompt("This assistant requires an access token:");
    if (token) {
      state.token = token.trim();
      sessionStorage.setItem("eka-token", state.token);
      return api(path, options);
    }
  }
  let data = null;
  try { data = await response.json(); } catch { /* non-JSON error */ }
  if (!response.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (HTTP ${response.status}).`);
  }
  return data;
}

// ── health ───────────────────────────────────────────────────────────────
async function loadHealth() {
  const box = $("health");
  try {
    const h = await api("/api/health");
    const gen = h.generator === "extractive" ? "extractive answers" : h.generator +
      (h.generator_ready === false ? " (unavailable, using extractive)" : "");
    box.textContent = `${h.searchable_documents} searchable · ${gen} · OCR ${h.ocr}`;
    box.title = `Embeddings: ${h.embedder}. Indexed passages: ${h.indexed_passages}.` +
      (h.generator_detail && h.generator_detail !== "ok" ? " " + h.generator_detail : "");
  } catch (error) {
    box.replaceChildren(el("span", "bad", error.message));
  }
}

// ── panels (dialogs that are side panels on wide screens) ─────────────────
function placePanels() {
  const library = $("library");
  if (narrow.matches) {
    if (library.open && !library.matches(":modal")) library.close();
  } else {
    if (library.open && library.matches(":modal")) library.close();
    if (!library.open) library.show();
  }
  const viewer = $("viewer");
  if (viewer.open && state.viewer) {
    const shouldBeModal = narrow.matches;
    if (viewer.matches(":modal") !== shouldBeModal) {
      viewer.close();
      shouldBeModal ? viewer.showModal() : viewer.show();
    }
  }
}
narrow.addEventListener("change", placePanels);
$("openLibrary").addEventListener("click", () => $("library").showModal());
document.querySelectorAll("[data-close]").forEach((button) =>
  button.addEventListener("click", () => {
    const dialog = $(button.dataset.close);
    dialog.close();
    if (dialog.id === "viewer") state.viewer = null;
  }));
$("viewer").addEventListener("close", () => { state.viewer = null; });

// ── library ──────────────────────────────────────────────────────────────
const STATUS_LABEL = { queued: "Queued", processing: "Processing", ready: "Ready", partial: "Partial", failed: "Failed" };
const PAGE_LABEL = {
  ok: "text", ocr: "OCR", ocr_low_conf: "OCR (low confidence)", empty: "blank",
  ocr_unavailable: "not read (needs OCR)", ocr_failed: "OCR failed", error: "error",
};

async function loadDocuments() {
  try {
    const data = await api("/api/documents");
    state.docs = data.documents;
  } catch (error) {
    $("uploads").replaceChildren(el("li", "error", error.message));
    return;
  }
  const known = new Set(state.docs.map((d) => d.id));
  for (const id of [...state.selected]) if (!known.has(id)) state.selected.delete(id);
  renderDocuments();
  const busy = state.docs.some((d) => d.status === "queued" || d.status === "processing");
  clearTimeout(state.pollTimer);
  if (busy) state.pollTimer = setTimeout(loadDocuments, 1000);
  else loadHealth();
}

function renderDocuments() {
  const list = $("doclist");
  const openPages = new Set([...list.querySelectorAll(".doc-pages:not([hidden])")].map((n) => n.closest(".doc").dataset.id));
  list.replaceChildren();
  $("libraryEmpty").hidden = state.docs.length > 0;
  const byId = Object.fromEntries(state.docs.map((d) => [d.id, d]));
  for (const doc of state.docs) {
    const node = $("tplDoc").content.firstElementChild.cloneNode(true);
    node.dataset.id = doc.id;
    if (doc.superseded_by) node.classList.add("superseded");
    node.querySelector(".doc-title").textContent = doc.title || doc.filename;
    const meta = [];
    if (doc.revision) meta.push("Rev " + doc.revision);
    if (doc.page_count) meta.push(`${doc.page_count} page${doc.page_count === 1 ? "" : "s"}`);
    if (doc.equipment) meta.push(doc.equipment);
    if (doc.superseded_by) meta.push("superseded by Rev " + ((byId[doc.superseded_by] || {}).revision || "?"));
    if (doc.title && doc.title !== doc.filename) meta.push(doc.filename);
    node.querySelector(".doc-meta").textContent = meta.join(" · ");
    const pill = node.querySelector(".pill");
    pill.textContent = STATUS_LABEL[doc.status] || doc.status;
    pill.classList.add(doc.status);

    const searchable = doc.status === "ready" || doc.status === "partial";
    const box = node.querySelector(".doc-select");
    box.disabled = !searchable;
    box.checked = state.selected.has(doc.id);
    box.addEventListener("change", () => {
      box.checked ? state.selected.add(doc.id) : state.selected.delete(doc.id);
      sessionStorage.setItem("eka-selected", JSON.stringify([...state.selected]));
      renderScope();
    });

    if (doc.status === "queued" || doc.status === "processing") {
      const bar = node.querySelector(".progress");
      bar.hidden = false;
      const pct = doc.progress_total ? Math.round((100 * doc.progress_done) / doc.progress_total) : 5;
      bar.firstElementChild.style.width = pct + "%";
      node.querySelector(".doc-meta").textContent = doc.stage || "Waiting to be processed";
    }
    const err = node.querySelector(".doc-error");
    const issues = pageIssues(doc);
    if (doc.error || issues) {
      err.hidden = false;
      err.textContent = doc.error || issues;
      if (doc.status !== "failed") err.classList.add("warn");
    }
    const actions = node.querySelector(".doc-actions");
    actions.querySelector('[data-act="view"]').hidden = !doc.page_count;
    actions.addEventListener("click", (event) => onDocAction(event, doc, node));
    list.append(node);
    if (openPages.has(doc.id)) showPages(doc, node);
  }
  renderScope();
}

function pageIssues(doc) {
  const s = doc.page_status || {};
  const parts = [];
  if (s.empty) parts.push(`${s.empty} blank page${s.empty > 1 ? "s" : ""}`);
  if (s.ocr || s.ocr_low_conf) parts.push(`${(s.ocr || 0) + (s.ocr_low_conf || 0)} page(s) read by OCR`);
  return parts.length && doc.status !== "failed" ? parts.join(" · ") : "";
}

function renderScope() {
  const scope = $("scope");
  const ready = state.docs.filter((d) => d.status === "ready" || d.status === "partial").length;
  scope.replaceChildren();
  if (!state.docs.length) return;
  if (state.selected.size) {
    scope.append(el("span", "", `Searching ${state.selected.size} selected document${state.selected.size > 1 ? "s" : ""}`));
    const clear = el("button", "link", "Search all");
    clear.type = "button";
    clear.addEventListener("click", () => {
      state.selected.clear();
      sessionStorage.removeItem("eka-selected");
      renderDocuments();
    });
    scope.append(clear);
  } else {
    scope.append(el("span", "", `Searching all ${ready} ready document${ready === 1 ? "" : "s"}. Tick documents to narrow the search.`));
  }
}

async function onDocAction(event, doc, node) {
  const act = event.target.dataset.act;
  if (!act) return;
  try {
    if (act === "view") openViewer({ docId: doc.id, page: 1, title: doc.title || doc.filename, pageCount: doc.page_count });
    if (act === "pages") {
      const panel = node.querySelector(".doc-pages");
      if (!panel.hidden) { panel.hidden = true; return; }
      await showPages(doc, node);
    }
    if (act === "reindex") {
      await api(`/api/documents/${doc.id}/reindex`, { method: "POST" });
      loadDocuments();
    }
    if (act === "delete") {
      if (!confirm(`Delete "${doc.title || doc.filename}" from the library? Its file and index entries are removed.`)) return;
      await api(`/api/documents/${doc.id}`, { method: "DELETE" });
      state.selected.delete(doc.id);
      loadDocuments();
    }
  } catch (error) {
    alert(error.message);
  }
}

async function showPages(doc, node) {
  const panel = node.querySelector(".doc-pages");
  const data = await api(`/api/documents/${doc.id}`);
  const table = el("table");
  const head = table.createTHead().insertRow();
  ["Page", "Read as", "Note"].forEach((t) => head.append(el("th", "", t)));
  const body = table.createTBody();
  for (const p of data.pages) {
    const row = body.insertRow();
    row.insertCell().textContent = p.page_no;
    row.insertCell().textContent = PAGE_LABEL[p.status] || p.status;
    const note = [p.reason];
    if (p.images && p.status === "ok") note.push(`${p.images} image(s) not interpreted`);
    row.insertCell().textContent = note.filter(Boolean).join("; ");
  }
  panel.replaceChildren(data.pages.length ? table : el("p", "muted", "No pages were read."));
  panel.hidden = false;
}

// ── upload ───────────────────────────────────────────────────────────────
function upload(file) {
  const item = el("li", "", `${file.name}: uploading…`);
  $("uploads").prepend(item);
  if (file.type && file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
    item.className = "error";
    item.textContent = `${file.name}: only PDF files are supported.`;
    return;
  }
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/documents?filename=" + encodeURIComponent(file.name));
  xhr.setRequestHeader("Content-Type", "application/pdf");
  if (state.token) xhr.setRequestHeader("Authorization", "Bearer " + state.token);
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) item.textContent = `${file.name}: uploading ${Math.round((100 * e.loaded) / e.total)}%`;
  };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
    if (xhr.status >= 400) {
      item.className = "error";
      item.textContent = `${file.name}: ${data.detail || "upload failed (HTTP " + xhr.status + ")"}`;
    } else {
      item.textContent = data.duplicate ? `${file.name}: ${data.message}` : `${file.name}: uploaded, processing…`;
      setTimeout(() => item.remove(), 8000);
    }
    loadDocuments();
  };
  xhr.onerror = () => {
    item.className = "error";
    item.textContent = `${file.name}: the server is not reachable.`;
  };
  xhr.send(file);
}

$("fileInput").addEventListener("change", (e) => {
  [...e.target.files].forEach(upload);
  e.target.value = "";
});
const zone = $("dropzone");
zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag"); });
zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
zone.addEventListener("drop", (e) => {
  e.preventDefault();
  zone.classList.remove("drag");
  [...e.dataTransfer.files].forEach(upload);
});

// ── chat ─────────────────────────────────────────────────────────────────
const STATUS_TAG = {
  answered: "From the documents", needs_clarification: "Needs clarification", conflict: "Sources conflict",
  insufficient_evidence: "Not established by the documents", error: "Error",
};

function addUser(text) {
  const li = el("li", "msg user", text);
  $("log").append(li);
  li.scrollIntoView({ block: "end" });
}

function renderAnswer(a, target) {
  const li = target || el("li");
  li.className = `msg bot s-${a.status}`;
  li.replaceChildren();
  if (STATUS_TAG[a.status]) li.append(el("span", "status-tag", STATUS_TAG[a.status]));
  if (a.message) li.append(el("p", "", a.message));
  const cites = Object.fromEntries((a.citations || []).map((c) => [c.id, c]));
  const number = Object.fromEntries((a.citations || []).map((c, i) => [c.id, i + 1]));

  const claimList = (claims) => {
    const ul = el("ul", "claims");
    for (const claim of claims) {
      const item = el("li", claim.kind === "warning" ? "warning" : "");
      if (claim.kind === "warning") item.append(el("span", "badge", "Safety"));
      if (claim.kind === "inference") item.append(el("span", "badge inference", "Inference, not stated directly"));
      if (claim.kind === "superseded") item.append(el("span", "badge", "Older revision"));
      item.append(document.createTextNode(claim.text));
      for (const id of claim.citations) {
        if (!cites[id]) continue;
        const chip = el("button", "cite", String(number[id]));
        chip.type = "button";
        const c = cites[id];
        chip.title = `${c.title}${c.revision ? " Rev " + c.revision : ""}, page ${c.page}`;
        chip.setAttribute("aria-label", `Open source ${number[id]}: ${chip.title}`);
        chip.addEventListener("click", () => openCitation(c));
        item.append(chip);
      }
      ul.append(item);
    }
    return ul;
  };

  if (a.claims && a.claims.length) li.append(claimList(a.claims));
  if (a.clarification) {
    const box = el("div", "callout clarify");
    box.append(el("strong", "", a.clarification.question));
    const options = el("div", "options");
    for (const option of a.clarification.options || []) {
      const b = el("button", "btn small", option);
      b.type = "button";
      b.addEventListener("click", () => ask(option));
      options.append(b);
    }
    if (options.childElementCount) box.append(options);
    li.append(box);
  }
  for (const conflict of a.conflicts || []) {
    const box = el("div", "callout conflict");
    box.append(el("strong", "", conflict.summary));
    if (conflict.claims && conflict.claims.length) box.append(claimList(conflict.claims));
    li.append(box);
  }
  if (a.unresolved && a.unresolved.length) {
    const box = el("div", "callout unresolved");
    box.append(el("strong", "", "Not established"));
    const ul = el("ul");
    a.unresolved.forEach((u) => ul.append(el("li", "", u)));
    box.append(ul);
    li.append(box);
  }
  if (a.safety_notice) li.append(el("div", "callout safety", a.safety_notice));

  if (a.citations && a.citations.length) {
    const sources = el("div", "sources");
    a.citations.forEach((c, i) => {
      const card = el("div", "source");
      const head = el("div", "source-head");
      head.append(el("b", "", `[${i + 1}]`), el("span", "", `${c.title}${c.revision ? " Rev " + c.revision : ""}`),
        el("span", "muted", `page ${c.page}${c.section ? " · " + c.section : ""}`));
      if (c.ocr) head.append(el("span", "badge", "OCR"));
      if (c.superseded) head.append(el("span", "badge", "Superseded revision"));
      card.append(head, el("p", "source-excerpt", "“" + c.excerpt + "”"));
      const actions = el("div", "source-actions");
      const view = el("button", "link", "View page");
      view.type = "button";
      view.addEventListener("click", () => openCitation(c));
      const pdf = el("a", "link", "Open PDF at page " + c.page);
      pdf.href = withToken(c.open_url);
      pdf.target = "_blank";
      pdf.rel = "noopener";
      actions.append(view, pdf);
      card.append(actions);
      sources.append(card);
    });
    li.append(sources);
  }

  if (a.intent === "question") {
    const more = el("details", "more");
    more.append(el("summary", "", "How this answer was found"));
    const lines = [];
    if (a.searched && a.searched.documents !== undefined)
      lines.push(`Searched ${a.searched.documents} document(s) (${a.searched.scope}).`);
    if (a.documents_used && a.documents_used.length)
      lines.push("Used: " + a.documents_used.map((d) => `${d.title}${d.revision ? " Rev " + d.revision : ""} (p. ${d.pages.join(", ")})`).join("; ") + ".");
    lines.push(`Answer method: ${a.generator}${a.fallback_reason ? " (model fallback: " + a.fallback_reason + ")" : ""}. ${a.latency_ms} ms.`);
    lines.forEach((t) => more.append(el("p", "", t)));
    const trace = a.trace || {};
    const rows = [...(trace.selected || []), ...(trace.rejected || [])];
    if (rows.length) {
      const titles = Object.fromEntries(state.docs.map((d) => [d.id, d.title || d.filename]));
      const table = el("table", "trace");
      const h = table.createTHead().insertRow();
      ["Document", "Page", "BM25", "Cosine", "Coverage", "Decision"].forEach((t) => h.append(el("th", "", t)));
      const body = table.createTBody();
      for (const r of rows) {
        const row = body.insertRow();
        [titles[r.doc_id] || r.doc_id, r.page, r.bm25, r.cosine, r.coverage, r.decision]
          .forEach((v) => (row.insertCell().textContent = v));
      }
      more.append(table);
    }
    li.append(more);
  }
  return li;
}

async function ask(text) {
  if (state.busy || !text.trim()) return;
  state.busy = true;
  $("send").disabled = true;
  addUser(text);
  const pending = el("li", "msg bot typing", "Searching the documents…");
  $("log").append(pending);
  pending.scrollIntoView({ block: "end" });
  try {
    const answer = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        conversation_id: state.conversationId,
        document_ids: state.selected.size ? [...state.selected] : null,
      }),
    });
    state.conversationId = answer.conversation_id;
    sessionStorage.setItem("eka-conversation", state.conversationId);
    renderAnswer(answer, pending);
    saveTranscript(text, answer);
  } catch (error) {
    renderAnswer({ status: "error", message: error.message, claims: [], citations: [] }, pending);
  } finally {
    state.busy = false;
    $("send").disabled = false;
    pending.scrollIntoView({ block: "nearest" });
  }
}

function saveTranscript(question, answer) {
  try {
    const items = JSON.parse(sessionStorage.getItem("eka-transcript") || "[]");
    items.push({ question, answer });
    sessionStorage.setItem("eka-transcript", JSON.stringify(items.slice(-30)));
  } catch { /* storage full or unavailable: transcript is a convenience */ }
}

function restoreTranscript() {
  try {
    for (const { question, answer } of JSON.parse(sessionStorage.getItem("eka-transcript") || "[]")) {
      addUser(question);
      $("log").append(renderAnswer(answer));
    }
  } catch { /* ignore */ }
}

$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const box = $("question");
  const text = box.value;
  box.value = "";
  autosize();
  ask(text);
});
$("question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});
function autosize() {
  if (CSS.supports("field-sizing", "content")) return;
  const box = $("question");
  box.style.height = "auto";
  box.style.height = Math.min(box.scrollHeight, 160) + "px";
}
$("question").addEventListener("input", autosize);
$("newChat").addEventListener("click", () => {
  state.conversationId = null;
  sessionStorage.removeItem("eka-conversation");
  sessionStorage.removeItem("eka-transcript");
  $("log").replaceChildren();
  $("question").focus();
});

for (const s of ["Hi, how are you?", "What is the rated pressure?", "How do I replace the shaft seal?", "Which documents do you have?"]) {
  const b = el("button", "btn small", s);
  b.type = "button";
  b.addEventListener("click", () => ask(s));
  $("suggestions").append(b);
}

// ── viewer ───────────────────────────────────────────────────────────────
function openCitation(c) {
  const doc = state.docs.find((d) => d.id === c.doc_id);
  openViewer({
    docId: c.doc_id, page: c.page, chunkId: c.chunk_id, quote: c.excerpt,
    title: `${c.title}${c.revision ? " Rev " + c.revision : ""}`, pageCount: doc ? doc.page_count : c.page,
  });
}

function openViewer(v) {
  state.viewer = { ...v, citedPage: v.page };
  const dialog = $("viewer");
  if (!dialog.open) narrow.matches ? dialog.showModal() : dialog.show();
  renderViewer();
}

function renderViewer() {
  const v = state.viewer;
  if (!v) return;
  $("viewerTitle").textContent = v.title;
  $("viewerMeta").textContent = v.chunkId && v.page === v.citedPage ? "Cited passage highlighted" : "";
  $("pageLabel").textContent = `Page ${v.page} of ${v.pageCount || "?"}`;
  $("prevPage").disabled = v.page <= 1;
  $("nextPage").disabled = v.pageCount ? v.page >= v.pageCount : false;
  $("openOriginal").href = withToken(`/api/documents/${v.docId}/file#page=${v.page}`);
  const quote = $("viewerQuote");
  quote.hidden = !(v.quote && v.page === v.citedPage);
  quote.textContent = v.quote ? "“" + v.quote + "”" : "";
  const img = $("pageImage");
  const loading = $("pageLoading");
  loading.hidden = false;
  loading.textContent = "Loading page…";
  const chunk = v.chunkId && v.page === v.citedPage ? "?chunk=" + encodeURIComponent(v.chunkId) : "";
  img.alt = `${v.title}, page ${v.page}`;
  img.onload = () => { loading.hidden = true; };
  img.onerror = () => { loading.textContent = "This page could not be rendered. Use “Open PDF at this page”."; };
  img.src = withToken(`/api/documents/${v.docId}/pages/${v.page}/image${chunk}`);
}

$("prevPage").addEventListener("click", () => { if (state.viewer && state.viewer.page > 1) { state.viewer.page--; renderViewer(); } });
$("nextPage").addEventListener("click", () => { if (state.viewer) { state.viewer.page++; renderViewer(); } });

// ── start ────────────────────────────────────────────────────────────────
placePanels();
restoreTranscript();
loadDocuments();
loadHealth();
