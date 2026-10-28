/* To Do: what needs a person, across the platform (dashboard/todo.py).
   One button in the top bar of every page, with a count of what needs action
   (red and amber items), and a panel listing each item with where to deal with
   it. Shared by the console and Clearance; styled only with the platform's
   tokens, so it follows Light and Dark. Reads /api/todo; changes nothing. */
(function () {
  const slot = document.querySelector("[data-todo-slot]");
  if (!slot || slot.dataset.ready) return;
  slot.dataset.ready = "1";
  const css = document.createElement("style");
  css.textContent = `
.todo{position:relative;display:inline-flex}
.todo-b{position:relative;width:34px;height:34px;border-radius:100px;border:1px solid var(--line);background:transparent;
  color:var(--mid);display:grid;place-items:center;cursor:pointer;transition:background-color .18s var(--e),color .18s var(--e)}
.todo-b:hover,.todo-b[aria-expanded="true"]{background:var(--surface-3);color:var(--ink)}
.todo-b svg{width:16px;height:16px}
.todo-n{position:absolute;top:-4px;right:-5px;min-width:17px;height:17px;padding:0 5px;border-radius:9px;background:var(--red);
  color:var(--surface);font:700 10.5px/17px var(--s);text-align:center;box-shadow:0 0 0 2px var(--bg)}
.todo-n[hidden]{display:none}
.todo-p{position:absolute;right:0;top:calc(100% + 10px);width:min(400px,calc(100vw - 24px));max-height:min(560px,calc(100vh - 96px));
  overflow:auto;background:var(--surface);color:var(--ink);border:1px solid var(--card-edge);border-radius:var(--r-lg,16px);
  box-shadow:0 18px 50px rgba(0,0,0,.18);z-index:90;opacity:0;transform:translateY(-4px);pointer-events:none;
  transition:opacity .16s var(--e),transform .16s var(--e)}
.todo-p.show{opacity:1;transform:none;pointer-events:auto}
.todo-h{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:14px 16px 10px;border-bottom:1px solid var(--line-2)}
.todo-h b{font-size:15px;font-weight:600}
.todo-h span{font-size:12px;color:var(--faint)}
.todo-l{list-style:none;margin:0;padding:6px}
.todo-l a{display:grid;grid-template-columns:10px minmax(0,1fr);gap:4px 10px;padding:9px 10px;border-radius:10px;text-decoration:none;color:inherit}
.todo-l a:hover,.todo-l a:focus-visible{background:var(--surface-3);outline:none}
.todo-l i{width:8px;height:8px;border-radius:50%;margin-top:6px;background:var(--faint)}
.todo-l i.red{background:var(--red)} .todo-l i.amber{background:var(--y-dot)}
.todo-l .t{font-size:13.5px;font-weight:600;line-height:1.35}
.todo-l .d{grid-column:2;font-size:12.5px;color:var(--mid);line-height:1.4}
.todo-l .s{grid-column:2;font-size:11px;color:var(--faint);letter-spacing:.02em}
.todo-e{padding:22px 16px;color:var(--faint);font-size:13px;text-align:center}
@media (prefers-reduced-motion:reduce){.todo-p{transition:none}}`;
  document.head.appendChild(css);
  slot.className = "todo";
  slot.innerHTML = `<button class="todo-b" type="button" aria-haspopup="true" aria-expanded="false" aria-controls="todoPanel" title="To Do: what needs a person">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11l2 2 4-4"/><rect x="3.5" y="3.5" width="17" height="17" rx="4"/></svg>
      <span class="todo-n" hidden></span><span class="sr" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)">To Do</span></button>
    <div class="todo-p" id="todoPanel" role="dialog" aria-label="To Do"><div class="todo-h"><b>To Do</b><span></span></div><ul class="todo-l"></ul></div>`;
  const btn = slot.querySelector(".todo-b"), panel = slot.querySelector(".todo-p"), badge = slot.querySelector(".todo-n");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const safe = (h) => (/^\/(?!\/)/.test(h || "") ? h : "/");
  // A link to where the page already is (same hash) still opens it.
  function follow(e, a) {
    const url = new URL(a.getAttribute("href"), location.href);
    if (url.origin === location.origin && url.pathname === location.pathname && url.hash === location.hash) {
      e.preventDefault(); dispatchEvent(new HashChangeEvent("hashchange"));
    }
  }
  let last = null;
  function paint(d) {
    last = d;
    const items = (d && d.items) || [];
    badge.hidden = !d || !d.count;
    badge.textContent = d && d.count > 99 ? "99+" : String((d && d.count) || "");
    btn.setAttribute("aria-label", "To Do" + (d && d.count ? ": " + d.count + " need action" : ""));
    panel.querySelector(".todo-h span").textContent = items.length ? `${d.count} need action · ${items.length} in all` : "";
    panel.querySelector(".todo-l").innerHTML = items.length ? items.map((i) =>
      `<li><a href="${esc(safe(i.href))}"><i class="${esc(i.level)}"></i><span class="t">${esc(i.title)}</span>` +
      `${i.detail ? `<span class="d">${esc(i.detail)}</span>` : ""}<span class="s">${esc(i.source)}</span></a></li>`).join("")
      : `<li class="todo-e">${d ? "Nothing needs a person right now." : "Could not load the list."}</li>`;
  }
  async function load() {
    try {
      const r = await fetch("/api/todo", {credentials: "same-origin"});
      if (r.status === 404) { slot.hidden = true; return; }      // not on this kind of server
      if (!r.ok) return paint(last);
      paint(await r.json());
    } catch (e) { paint(last); }
  }
  function open(on) {
    panel.classList.toggle("show", on);
    btn.setAttribute("aria-expanded", String(on));
    if (on) load();
  }
  btn.addEventListener("click", (e) => { e.stopPropagation(); open(!panel.classList.contains("show")); });
  document.addEventListener("click", (e) => { if (!slot.contains(e.target)) open(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && panel.classList.contains("show")) { e.stopPropagation(); open(false); btn.focus(); }
  }, true);
  // Close, and follow the link even when it is where the page already is.
  panel.addEventListener("click", (e) => { const a = e.target.closest("a"); if (a) { open(false); follow(e, a); } });
  load();
  setInterval(() => { if (!document.hidden) load(); }, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  window.ctTodo = {reload: load};
})();
