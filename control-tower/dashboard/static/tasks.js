/* Tasks: the Overview's "Action required" card and the Task Center page.
   Both read GET /api/todo (dashboard/todo.py: derived from the platform's
   own records, filtered by access level); nothing is stored or counted here.
   Each task opens where it is dealt with. Styled with the platform's tokens
   and the console's card, chip and status-pill patterns. */
(function () {
  const card = document.querySelector("[data-action-card]");
  const center = document.querySelector("[data-task-center]");
  if (!card && !center) return;
  const css = document.createElement("style");
  css.textContent = `
.tk-sum{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:var(--faint)}
.tk-sum b{color:var(--ink);font-weight:600}
.tk-list{list-style:none;margin:0;padding:0}
.tk-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 16px;align-items:center;padding:12px 2px;border-bottom:1px solid var(--line-2);
  color:inherit;text-decoration:none;border-radius:0}
.tk-list li:last-child .tk-row{border-bottom:0}
.tk-row:hover .tk-t,.tk-row:focus-visible .tk-t{color:var(--vio)}
.tk-row:focus-visible{outline:2px solid var(--vio);outline-offset:2px;border-radius:8px}
.tk-t,.tk-d,.tk-m{display:block}
.tk-t{font-size:13.5px;font-weight:600;line-height:1.35;color:var(--ink);overflow-wrap:anywhere}
.tk-d{font-size:12.5px;color:var(--mid);line-height:1.4;margin-top:2px;overflow-wrap:anywhere}
.tk-m{font-size:11.5px;color:var(--faint);margin-top:3px}
.tk-r{display:flex;flex-direction:column;align-items:flex-end;gap:4px;white-space:nowrap}
.tk-st{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:3px 9px;font-size:11.5px;font-weight:500;
  background:var(--surface-3);color:var(--mid)}
.tk-st::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.tk-st.s-overdue{background:var(--red-wash);color:var(--red)}
.tk-st.s-urgent{background:var(--y-wash);color:var(--y)}
.tk-st.s-pending_approval{background:var(--blu-wash);color:var(--blu)}
.tk-st.s-completed{background:var(--grn-wash);color:var(--grn)}
.tk-due{font-size:11.5px;color:var(--faint)}
.tk-due.late{color:var(--red)}
.tk-empty{padding:18px 2px;color:var(--faint);font-size:13px}
.tk-foot{display:flex;justify-content:flex-end;padding-top:12px}
.tk-foot a,.tk-link{font-size:13px;font-weight:500;color:var(--vio);text-decoration:none}
.tk-foot a:hover,.tk-link:hover{text-decoration:underline}
.tk-filters{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 6px}
.tk-filters button{border:1px solid transparent;background:transparent;color:var(--mid);border-radius:999px;padding:6px 12px;font:500 12.5px var(--s);
  cursor:pointer;transition:background-color .18s var(--e),color .18s var(--e)}
.tk-filters button:hover{background:var(--surface-3);color:var(--ink)}
.tk-filters button[aria-pressed="true"]{background:var(--vio-wash);color:var(--vio)}
.tk-filters button span{margin-left:6px;color:var(--faint);font-variant-numeric:tabular-nums}
.tk-src{font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--faint)}
@media (max-width:640px){.tk-row{grid-template-columns:minmax(0,1fr)}.tk-r{flex-direction:row;align-items:center;justify-content:flex-start;flex-wrap:wrap}}`;
  document.head.appendChild(css);

  const LABEL = {overdue: "Overdue", urgent: "Urgent", pending_approval: "Pending approval", open: "To do", completed: "Completed"};
  const FILTERS = [["outstanding", "All outstanding"], ["overdue", "Overdue"], ["urgent", "Urgent"],
                   ["pending_approval", "Pending approval"], ["open", "To do"], ["completed", "Completed"]];
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const safe = (h) => (/^\/(?!\/)/.test(h || "") ? h : "/");
  const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  function when(stamp) {
    const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(String(stamp || ""));
    if (!m) return null;
    const d = `${+m[3]} ${MON[+m[2] - 1]} ${m[1]}`;
    const today = new Date(), iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    return m[4] && `${m[1]}-${m[2]}-${m[3]}` === iso ? `today ${m[4]}:${m[5]}` : d;
  }
  function row(t) {
    const due = t.state !== "completed" && t.due ? when(t.due) : null;
    const done = t.state === "completed" && t.at ? when(t.at) : null;
    return `<li><a class="tk-row" href="${esc(safe(t.href))}">
      <span><span class="tk-t">${esc(t.title)}</span>${t.detail ? `<span class="tk-d">${esc(t.detail)}</span>` : ""}
        <span class="tk-m"><span class="tk-src">${esc(t.source)}</span>${t.count > 1 ? ` · ${Number(t.count).toLocaleString("en-US")} records` : ""}</span></span>
      <span class="tk-r"><span class="tk-st s-${esc(t.state)}">${esc(LABEL[t.state] || t.state)}</span>
        ${due ? `<span class="tk-due${t.state === "overdue" ? " late" : ""}">Due ${esc(due)}</span>` : ""}${done ? `<span class="tk-due">Done ${esc(done)}</span>` : ""}</span></a></li>`;
  }
  // A link to where the page already is (same hash) still opens it.
  document.addEventListener("click", (e) => {
    const a = e.target.closest(".tk-row, [data-tasks-link]");
    if (!a) return;
    const url = new URL(a.getAttribute("href"), location.href);
    if (url.origin === location.origin && url.pathname === location.pathname && url.hash === location.hash) {
      e.preventDefault(); dispatchEvent(new HashChangeEvent("hashchange"));
    }
  });

  let data = null, filter = "outstanding";
  function paintCard() {
    if (!card || !data) return;
    const body = card.querySelector("[data-action-body]"), sum = card.querySelector("[data-action-sum]");
    const top = data.items.filter((t) => t.state !== "completed").slice(0, 3);
    const b = data.by_state || {};
    sum.innerHTML = data.outstanding
      ? `<span><b>${data.outstanding}</b> outstanding</span>` +
        [["overdue", "overdue"], ["urgent", "urgent"], ["pending_approval", "pending approval"]]
          .filter(([k]) => b[k]).map(([k, l]) => `<span><b>${b[k]}</b> ${l}</span>`).join("")
      : "Nothing outstanding";
    body.innerHTML = (top.length ? `<ul class="tk-list">${top.map(row).join("")}</ul>`
      : `<div class="tk-empty">Nothing needs a person right now.</div>`) +
      `<div class="tk-foot"><a href="/#tasks" data-tasks-link>View all tasks${data.outstanding > 3 ? ` (${data.outstanding})` : ""} →</a></div>`;
    card.hidden = false;
  }
  function paintCenter() {
    if (!center || !data) return;
    const b = data.by_state || {};
    const n = (k) => (k === "outstanding" ? data.outstanding : b[k] || 0);
    const list = data.items.filter((t) => (filter === "outstanding" ? t.state !== "completed" : t.state === filter));
    center.innerHTML = `<div class="tk-filters" role="group" aria-label="Show tasks">` +
      FILTERS.map(([k, l]) => `<button type="button" data-f="${k}" aria-pressed="${filter === k}">${l}<span>${n(k)}</span></button>`).join("") +
      `</div>` + (list.length ? `<ul class="tk-list">${list.map(row).join("")}</ul>`
        : `<div class="tk-empty">${filter === "completed" ? "Nothing completed in the last 7 days." : "Nothing here. Tasks appear when a record needs a person."}</div>`);
  }
  if (center) center.addEventListener("click", (e) => {
    const f = e.target.closest("[data-f]");
    if (!f) return;
    filter = f.dataset.f; paintCenter();
    const again = center.querySelector(`[data-f="${filter}"]`); if (again) again.focus();
  });
  function navBadge() {
    ["n-tasks", "t-tasks"].forEach((id) => {
      const el = document.getElementById(id);
      if (!el || !data) return;
      el.hidden = !data.outstanding; el.textContent = String(data.outstanding || "");
    });
  }
  async function load() {
    try {
      const r = await fetch("/api/todo", {credentials: "same-origin"});
      if (r.status === 404) { if (card) card.hidden = true; return; }
      if (!r.ok) throw new Error("HTTP " + r.status);
      data = await r.json();
      paintCard(); paintCenter(); navBadge();
    } catch (e) {
      if (center && !data) center.innerHTML = `<div class="tk-empty">The tasks could not be loaded (${esc(e.message || e)}).</div>`;
    }
  }
  load();
  setInterval(() => { if (!document.hidden) load(); }, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  addEventListener("hashchange", () => { if (/^#tasks\b/.test(location.hash)) load(); });
  window.ctTasks = {reload: load};
})();
