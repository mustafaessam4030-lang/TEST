/* Notifications: the bell in the top bar and its compact popover.
   Recent events from GET /api/notifications (dashboard/todo.py: Human Action
   opened or finished, escalations opened or resolved, an export loaded, a
   run finished). Separate from tasks: the footer leads to the Task Center.
   "Read" is this viewer's own (kept in this browser); nothing here changes a
   record. Closes on Escape (focus back to the bell), a click outside, Tab
   leaving it, or picking an item. Styled with the platform's tokens. */
(function () {
  const slot = document.querySelector("[data-notify-slot]");
  if (!slot || slot.dataset.ready) return;
  slot.dataset.ready = "1";
  const css = document.createElement("style");
  css.textContent = `
.nt{position:relative;display:inline-flex}
.nt-b{position:relative;width:34px;height:34px;border-radius:100px;border:1px solid var(--line);background:transparent;color:var(--mid);
  display:grid;place-items:center;cursor:pointer;transition:background-color .18s var(--e),color .18s var(--e)}
.nt-b:hover,.nt-b[aria-expanded="true"]{background:var(--surface-3);color:var(--ink)}
.nt-b:focus-visible{outline:2px solid var(--vio);outline-offset:2px}
.nt-b svg{width:16px;height:16px}
.nt-n{position:absolute;top:-4px;right:-5px;min-width:17px;height:17px;padding:0 5px;border-radius:9px;background:var(--red);
  color:var(--surface);font:700 10.5px/17px var(--s);text-align:center;box-shadow:0 0 0 2px var(--bg)}
.nt-n[hidden]{display:none}
.nt-p{position:fixed;width:min(340px,calc(100vw - 16px));max-height:min(440px,calc(100vh - 88px));display:flex;flex-direction:column;
  background:var(--surface);color:var(--ink);border:1px solid var(--card-edge);border-radius:14px;box-shadow:0 14px 40px rgba(0,0,0,.16);
  z-index:96;opacity:0;transform:translateY(-4px);pointer-events:none;visibility:hidden;
  transition:opacity .14s var(--e),transform .14s var(--e),visibility 0s linear .14s}
.nt-p.show{opacity:1;transform:none;pointer-events:auto;visibility:visible;transition:opacity .14s var(--e),transform .14s var(--e)}
.nt-h{display:flex;align-items:center;gap:10px;padding:12px 14px 10px;border-bottom:1px solid var(--line-2)}
.nt-h b{font-size:14px;font-weight:600}
.nt-h button{margin-left:auto;border:0;background:transparent;color:var(--vio);font:500 12px var(--s);cursor:pointer;padding:4px 6px;border-radius:6px}
.nt-h button:hover{background:var(--vio-wash)}
.nt-h button:focus-visible,.nt-l a:focus-visible,.nt-f a:focus-visible{outline:2px solid var(--vio);outline-offset:-2px}
.nt-l{list-style:none;margin:0;padding:4px;overflow:auto;flex:1}
.nt-l a{display:grid;grid-template-columns:8px minmax(0,1fr);gap:2px 10px;padding:8px 10px;border-radius:9px;text-decoration:none;color:inherit}
.nt-l a:hover{background:var(--surface-3)}
.nt-l i{width:7px;height:7px;border-radius:50%;margin-top:6px;background:transparent}
.nt-l a.unread i{background:var(--vio)}
.nt-l .t{font-size:13px;font-weight:600;line-height:1.35;overflow-wrap:anywhere}
.nt-l .d{grid-column:2;font-size:12px;color:var(--mid);overflow-wrap:anywhere}
.nt-l .w{grid-column:2;font-size:11px;color:var(--faint)}
.nt-e{padding:18px 14px;color:var(--faint);font-size:13px}
.nt-f{border-top:1px solid var(--line-2);padding:10px 14px}
.nt-f a{font-size:13px;font-weight:500;color:var(--vio);text-decoration:none;border-radius:6px}
.nt-f a:hover{text-decoration:underline}
@media (prefers-reduced-motion:reduce){.nt-p,.nt-p.show{transition:none}}`;
  document.head.appendChild(css);
  slot.className = "nt";
  slot.innerHTML = `<button class="nt-b" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="ntPanel" aria-label="Notifications" title="Notifications">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>
    <span class="nt-n" hidden></span></button>`;
  // On the page itself, placed under the bell: the top bar cannot clip it.
  const panel = document.createElement("div");
  panel.className = "nt-p"; panel.id = "ntPanel"; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Notifications");
  panel.innerHTML = `<div class="nt-h"><b>Notifications</b><button type="button" data-read>Mark all as read</button></div>
    <ul class="nt-l"></ul><div class="nt-f"><a href="/#tasks" data-tasks-link>Open the Task Center</a></div>`;
  document.body.appendChild(panel);
  const btn = slot.querySelector(".nt-b"), badge = slot.querySelector(".nt-n"), list = panel.querySelector(".nt-l");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const safe = (h) => (/^\/(?!\/)/.test(h || "") ? h : "/");
  const KEY = "ata-notify-seen";
  const seen = () => { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } };
  const markSeen = (at) => { try { if (at) localStorage.setItem(KEY, at); } catch (e) { /* private window */ } };
  const norm = (s) => String(s || "").replace(" ", "T").slice(0, 19);
  let items = [], loaded = false;

  function ago(at) {
    const t = Date.parse(norm(at)); if (!t) return "";
    const m = Math.round((Date.now() - t) / 60000);
    return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
  }
  function paint() {
    const last = seen();
    const unread = items.filter((n) => norm(n.at) > last).length;
    badge.hidden = !unread; badge.textContent = unread > 99 ? "99+" : String(unread || "");
    btn.setAttribute("aria-label", "Notifications" + (unread ? `: ${unread} unread` : ""));
    panel.querySelector("[data-read]").hidden = !unread;
    list.innerHTML = items.length ? items.map((n) =>
      `<li><a href="${esc(safe(n.href))}" class="${norm(n.at) > last ? "unread" : ""}"><i aria-hidden="true"></i><span class="t">${esc(n.title)}</span>` +
      `${n.detail ? `<span class="d">${esc(n.detail)}</span>` : ""}<span class="w">${esc(ago(n.at))}</span></a></li>`).join("")
      : `<li class="nt-e">${loaded ? "No notifications in the last 7 days." : "Loading…"}</li>`;
  }
  async function load() {
    try {
      const r = await fetch("/api/notifications", {credentials: "same-origin"});
      if (r.status === 404) { slot.hidden = true; return; }      // not on this kind of server
      if (!r.ok) return;
      items = (await r.json()).items || []; loaded = true; paint();
    } catch (e) { /* keep what is shown */ }
  }
  function place() {                  // under the bell, always inside the screen
    const r = btn.getBoundingClientRect(), w = panel.offsetWidth || 340;
    panel.style.top = Math.round(r.bottom + 8) + "px";
    panel.style.left = Math.round(Math.min(Math.max(8, r.right - w), innerWidth - w - 8)) + "px";
  }
  function open(on, focusBack) {
    panel.classList.toggle("show", on);
    btn.setAttribute("aria-expanded", String(on));
    if (on) {
      place(); paint();
      setTimeout(() => { const f = panel.querySelector("[data-read]:not([hidden]), .nt-l a, .nt-f a"); if (f) f.focus(); }, 40);
      load();
    } else if (focusBack) btn.focus();
  }
  btn.addEventListener("click", (e) => { e.stopPropagation(); open(!panel.classList.contains("show"), false); });
  panel.querySelector("[data-read]").addEventListener("click", () => { if (items[0]) markSeen(norm(items[0].at)); paint(); btn.focus(); });
  panel.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (!a) return;
    open(false, false);
    const url = new URL(a.getAttribute("href"), location.href);           // same place: still open it
    if (url.origin === location.origin && url.pathname === location.pathname && url.hash === location.hash) {
      e.preventDefault(); dispatchEvent(new HashChangeEvent("hashchange"));
    }
  });
  document.addEventListener("click", (e) => { if (panel.classList.contains("show") && !panel.contains(e.target) && !slot.contains(e.target)) open(false, false); });
  // Escape closes this popover and nothing else (captured first).
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && panel.classList.contains("show")) { e.stopPropagation(); open(false, true); }
  }, true);
  panel.addEventListener("focusout", (e) => { if (e.relatedTarget && !panel.contains(e.relatedTarget) && e.relatedTarget !== btn) open(false, false); });
  addEventListener("resize", () => { if (panel.classList.contains("show")) place(); });
  load();
  setInterval(() => { if (!document.hidden) load(); }, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
})();
