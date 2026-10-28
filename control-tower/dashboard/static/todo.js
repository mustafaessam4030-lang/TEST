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
.todo-p{position:fixed;top:var(--barh,64px);right:0;bottom:0;width:min(340px,100vw);display:flex;flex-direction:column;
  background:var(--surface);color:var(--ink);border-left:1px solid var(--line);box-shadow:-12px 0 32px rgba(0,0,0,.08);z-index:85;
  transform:translateX(102%);visibility:hidden;transition:transform .24s var(--e),visibility 0s linear .24s}
.todo-p.show{transform:none;visibility:visible;transition:transform .24s var(--e),visibility 0s}
.todo-x{margin-left:auto;width:28px;height:28px;border-radius:8px;border:0;background:transparent;color:var(--faint);cursor:pointer;font-size:18px;line-height:1}
.todo-x:hover{background:var(--surface-3);color:var(--ink)}
.todo-l{flex:1;overflow:auto}
.todo-h{display:flex;align-items:center;gap:10px;padding:14px 12px 12px 16px;border-bottom:1px solid var(--line-2)}
.todo-h b{font-size:15px;font-weight:600}
.todo-h span{font-size:12px;color:var(--faint)}
.todo-l{list-style:none;margin:0;padding:6px}
.todo-l a{display:grid;grid-template-columns:10px minmax(0,1fr);gap:2px 10px;padding:8px 10px;border-radius:10px;text-decoration:none;color:inherit}
.todo-l a:hover,.todo-l a:focus-visible{background:var(--surface-3);outline:none}
.todo-l i{width:8px;height:8px;border-radius:50%;margin-top:6px;background:var(--faint)}
.todo-l i.red{background:var(--red)} .todo-l i.amber{background:var(--y-dot)}
.todo-l .t{font-size:13px;font-weight:600;line-height:1.35}
.todo-l .d{grid-column:2;font-size:12px;color:var(--mid);line-height:1.4}
.todo-l .s{grid-column:2;font-size:11px;color:var(--faint);letter-spacing:.02em}
.todo-e{padding:22px 16px;color:var(--faint);font-size:13px;text-align:center}
@media (prefers-reduced-motion:reduce){.todo-p,.todo-p.show{transition:none}}`;
  document.head.appendChild(css);
  slot.className = "todo";
  slot.innerHTML = `<button class="todo-b" type="button" aria-haspopup="true" aria-expanded="false" aria-controls="todoPanel" title="To Do: what needs a person">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11l2 2 4-4"/><rect x="3.5" y="3.5" width="17" height="17" rx="4"/></svg>
      <span class="todo-n" hidden></span><span class="sr" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)">To Do</span></button>
`;
  // A side panel on the right, under the top bar: the page stays usable
  // beside it. On the page itself (not in the bar), so nothing clips it.
  const panel = document.createElement("aside");
  panel.className = "todo-p"; panel.id = "todoPanel"; panel.setAttribute("aria-label", "To Do");
  panel.innerHTML = `<div class="todo-h"><b>To Do</b><span></span><button class="todo-x" type="button" aria-label="Close To Do">×</button></div><ul class="todo-l"></ul>`;
  document.body.appendChild(panel);
  const btn = slot.querySelector(".todo-b"), badge = slot.querySelector(".todo-n");
  panel.querySelector(".todo-x").addEventListener("click", () => { open(false); btn.focus(); });
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
