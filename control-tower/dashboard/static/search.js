/* Search: one box for an AWB, BOL or PO reference, wherever it is
   (dashboard/search.py: the run, the Clearance export, PO jobs). A button in
   the top bar of every page; "/" or Ctrl+K opens it. Each result opens where
   it lives. Styled only with the platform's tokens. Reads /api/search. */
(function () {
  const slot = document.querySelector("[data-search-slot]");
  if (!slot || slot.dataset.ready) return;
  slot.dataset.ready = "1";
  const css = document.createElement("style");
  css.textContent = `
.srch{position:relative;display:inline-flex}
.srch-b{height:34px;border-radius:100px;border:1px solid var(--line);background:transparent;color:var(--mid);
  display:inline-flex;align-items:center;gap:8px;padding:0 12px 0 10px;cursor:pointer;font:500 12.5px var(--s);
  transition:background-color .18s var(--e),color .18s var(--e)}
.srch-b:hover,.srch-b[aria-expanded="true"]{background:var(--surface-3);color:var(--ink)}
.srch-b svg{width:15px;height:15px}
.srch-b kbd{font:600 10.5px var(--mono);color:var(--faint);border:1px solid var(--line);border-radius:5px;padding:0 5px;line-height:16px}
.srch-p{position:absolute;right:0;top:calc(100% + 10px);width:min(520px,calc(100vw - 24px));background:var(--surface);color:var(--ink);
  border:1px solid var(--card-edge);border-radius:var(--r-lg,16px);box-shadow:0 18px 50px rgba(0,0,0,.18);z-index:91;
  opacity:0;transform:translateY(-4px);pointer-events:none;transition:opacity .16s var(--e),transform .16s var(--e)}
.srch-p.show{opacity:1;transform:none;pointer-events:auto}
.srch-i{display:flex;align-items:center;gap:10px;padding:12px 14px;border-bottom:1px solid var(--line-2)}
.srch-i svg{width:16px;height:16px;color:var(--faint);flex:none}
.srch-i input{flex:1;border:0;outline:0;background:transparent;color:var(--ink);font:400 15px var(--s);min-width:0}
.srch-r{max-height:min(460px,calc(100vh - 160px));overflow:auto;padding:6px}
.srch-g{font:var(--t-label,600 11px/1.2 var(--s));letter-spacing:.06em;text-transform:uppercase;color:var(--faint);padding:10px 10px 4px}
.srch-r a{display:block;padding:8px 10px;border-radius:10px;text-decoration:none;color:inherit}
.srch-r a:hover,.srch-r a.on{background:var(--surface-3)}
.srch-r a b{display:block;font:600 13.5px var(--mono)}
.srch-r a span{display:block;font-size:12.5px;color:var(--mid);margin-top:1px}
.srch-e{padding:18px 14px;color:var(--faint);font-size:13px}
@media (max-width:1600px){.srch-b span,.srch-b kbd{display:none}.srch-b{width:34px;padding:0;justify-content:center}}
@media (prefers-reduced-motion:reduce){.srch-p{transition:none}}`;
  document.head.appendChild(css);
  slot.className = "srch";
  const icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>';
  slot.innerHTML = `<button class="srch-b" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="srchPanel" title="Search an AWB, BOL or PO (/)">${icon}<span>Search</span><kbd>/</kbd></button>
    <div class="srch-p" id="srchPanel" role="dialog" aria-label="Search">
      <div class="srch-i">${icon}<input type="search" placeholder="AWB, BOL or PO reference" aria-label="Search an AWB, BOL or PO reference" autocomplete="off" spellcheck="false" maxlength="80"></div>
      <div class="srch-r" role="listbox"></div></div>`;
  const btn = slot.querySelector(".srch-b"), panel = slot.querySelector(".srch-p"),
        input = slot.querySelector("input"), out = slot.querySelector(".srch-r");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const safe = (h) => (/^\/(?!\/)/.test(h || "") ? h : "/");
  let timer = null, seq = 0, active = -1;
  const hint = () => { out.innerHTML = `<div class="srch-e">Type at least 3 characters of an AWB, BOL or PO reference.</div>`; };
  hint();
  function paint(d) {
    const groups = (d && d.groups) || [];
    active = -1;
    if (!d || !d.searched) return hint();
    out.innerHTML = groups.length ? groups.map((g) => `<div class="srch-g">${esc(g.source)}</div>` +
      g.items.map((i) => `<a href="${esc(safe(i.href))}" role="option"><b>${esc(i.title)}</b><span>${esc(i.detail)}</span></a>`).join("")).join("")
      : `<div class="srch-e">Nothing found for “${esc(d.query)}” in the run, the Clearance export or PO jobs.</div>`;
  }
  async function run() {
    const q = input.value.trim(), my = ++seq;
    if (q.replace(/[^0-9a-z]/gi, "").length < 3) return hint();
    try {
      const r = await fetch("/api/search?q=" + encodeURIComponent(q), {credentials: "same-origin"});
      const d = r.ok ? await r.json() : null;
      if (my === seq) paint(d || {searched: true, query: q, groups: []});
    } catch (e) { if (my === seq) out.innerHTML = `<div class="srch-e">Search did not reach the server.</div>`; }
  }
  function open(on) {
    panel.classList.toggle("show", on);
    btn.setAttribute("aria-expanded", String(on));
    if (on) setTimeout(() => { input.focus(); input.select(); }, 30);
  }
  function move(step) {
    const links = [...out.querySelectorAll("a")];
    if (!links.length) return;
    active = (active + step + links.length) % links.length;
    links.forEach((a, i) => a.classList.toggle("on", i === active));
    links[active].scrollIntoView({block: "nearest"});
  }
  btn.addEventListener("click", (e) => { e.stopPropagation(); open(!panel.classList.contains("show")); });
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 180); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") {
      const links = [...out.querySelectorAll("a")], pick = links[Math.max(active, 0)];
      if (pick) { e.preventDefault(); open(false); location.href = pick.getAttribute("href"); }
    }
  });
  document.addEventListener("click", (e) => { if (!slot.contains(e.target)) open(false); });
  panel.addEventListener("click", (e) => { if (e.target.closest("a")) open(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && panel.classList.contains("show")) { open(false); btn.focus(); return; }
    // Never a key someone else already took: the Human Action live view
    // forwards every key to the carrier's page (and marks it handled).
    if (e.defaultPrevented || (e.target && e.target.closest && e.target.closest("#rvStage, [data-own-keys]"))) return;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || "") || (e.target && e.target.isContentEditable);
    if ((e.key === "/" && !typing) || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k")) { e.preventDefault(); open(true); }
  });
})();
