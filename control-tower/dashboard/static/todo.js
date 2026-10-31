/* To Do: what needs a person, across the platform (dashboard/todo.py).
   One button in the top bar of every page, with a count of what needs action
   (red and amber items), and a panel listing each item with where to deal with
   it; on the Overview, the same list as a card ([data-todo-card]). Shared by
   the console and Clearance; styled only with the platform's tokens, so it
   follows Light and Dark. Reads /api/todo; changes nothing. */
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
.todo-p{position:fixed;top:calc(var(--barh,64px) + 8px);right:16px;width:min(340px,calc(100vw - 16px));
  max-height:min(480px,calc(100vh - var(--barh,64px) - 24px));display:flex;flex-direction:column;
  background:var(--surface);color:var(--ink);border:1px solid var(--card-edge);border-radius:16px;box-shadow:0 12px 34px rgba(0,0,0,.12),0 2px 6px rgba(0,0,0,.04);z-index:85;
  opacity:0;transform:translateX(24px);visibility:hidden;transition:transform .26s var(--e),opacity .26s var(--e),visibility 0s linear .26s}
.todo-p.show{opacity:1;transform:none;visibility:visible;transition:transform .26s var(--e),opacity .26s var(--e),visibility 0s}
.todo-x{margin-left:auto;width:28px;height:28px;border-radius:50%;border:0;background:var(--surface-3);color:var(--mid);cursor:pointer;font-size:16px;line-height:1}
.todo-x:hover{background:var(--surface-3);color:var(--ink)}
.todo-l{flex:1;overflow:auto}
.todo-h{display:flex;align-items:center;gap:10px;padding:10px 10px 9px 14px;border-bottom:1px solid var(--line-2)}
.todo-h b{font-size:14px;font-weight:600}
.todo-h span{font-size:12px;color:var(--faint)}
.todo-l{list-style:none;margin:0;padding:4px}
.todo-l a{display:grid;grid-template-columns:8px minmax(0,1fr);gap:1px 10px;padding:7px 10px;border-radius:9px;text-decoration:none;color:inherit}
.todo-l a:hover,.todo-l a:focus-visible{background:var(--surface-3);outline:none}
.todo-l i{width:7px;height:7px;border-radius:50%;margin-top:6px;background:var(--faint)}
.todo-l i.red{background:var(--red)} .todo-l i.amber{background:var(--y-dot)}
.todo-l .t{font-size:13px;font-weight:600;line-height:1.35}
.todo-l .d{grid-column:2;font-size:12px;color:var(--mid);line-height:1.4;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.todo-l .st{font-weight:600}
.todo-l .st.overdue{color:var(--red)} .todo-l .st.urgent{color:var(--y)} .todo-l .st.pending_approval{color:var(--blu)}
.todo-l .s{grid-column:2;font-size:11px;color:var(--faint);letter-spacing:.02em}
.todo-e{padding:22px 16px;color:var(--faint);font-size:13px;text-align:center}
@media (prefers-reduced-motion:reduce){.todo-p,.todo-p.show{transition:none}}`;
  document.head.appendChild(css);
  slot.className = "todo";
  slot.innerHTML = `<button class="todo-b" type="button" aria-haspopup="true" aria-expanded="false" aria-controls="todoPanel" title="To Do: what needs a person">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11l2 2 4-4"/><rect x="3.5" y="3.5" width="17" height="17" rx="4"/></svg>
      <span class="todo-n" hidden></span><span class="sr" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)">To Do</span></button>
`;
  // The To Do slide-out: compact, under its button, sliding in from the
  // right. On the page itself (not in the bar), so nothing clips it.
  const panel = document.createElement("aside");
  panel.className = "todo-p"; panel.id = "todoPanel"; panel.setAttribute("aria-label", "To Do");
  panel.innerHTML = `<div class="todo-h"><b>To Do</b><span></span><button class="todo-x" type="button" aria-label="Close To Do">×</button></div><ul class="todo-l"></ul>`;
  document.body.appendChild(panel);
  const btn = slot.querySelector(".todo-b"), badge = slot.querySelector(".todo-n");
  // The Overview's To Do card: the same list, always open.
  const card = document.querySelector("[data-todo-card]");
  const cardList = card && card.querySelector("[data-todo-list]");
  const cardSum = card && card.querySelector("[data-todo-sum]");
  // Compact, under its own button, always inside the screen.
  function place() {
    const r = btn.getBoundingClientRect(), w = panel.offsetWidth || 340;
    panel.style.top = Math.round(r.bottom + 8) + "px";
    panel.style.left = Math.round(Math.min(Math.max(8, r.right - w), innerWidth - w - 8)) + "px";
    panel.style.right = "auto";
  }
  panel.querySelector(".todo-x").addEventListener("click", () => { open(false); btn.focus(); });
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const safe = (h) => (/^\/(?![\/\\])/.test(h || "") ? h : "/");
  // A link to where the page already is (same hash) still opens it.
  function follow(e, a) {
    const url = new URL(a.getAttribute("href"), location.href);
    if (url.origin === location.origin && url.pathname === location.pathname && url.hash === location.hash) {
      e.preventDefault(); dispatchEvent(new HashChangeEvent("hashchange"));
    }
  }
  const STATE = {overdue: "Overdue", urgent: "Urgent", pending_approval: "Pending approval", open: "To do"};
  const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const day = (v) => { const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(String(v || "")); return m ? `${+m[3]} ${MON[+m[2] - 1]}${m[4] ? " " + m[4] + ":" + m[5] : ""}` : ""; };
  let last = null;
  function paint(d) {
    last = d;
    // What is still to do; finished ones are not listed here.
    const items = ((d && d.items) || []).filter((i) => i.state !== "completed");
    badge.hidden = !d || !d.count;
    badge.textContent = d && d.count > 99 ? "99+" : String((d && d.count) || "");
    btn.setAttribute("aria-label", "To Do" + (d && d.count ? ": " + d.count + " need action" : ""));
    const sum = items.length ? `${d.count} need action · ${items.length} in all` : "";
    const html = items.length ? items.map((i) =>
      `<li><a href="${esc(safe(window.TodoHref ? window.TodoHref(i.href) : i.href))}"><i class="${esc(i.level)}"></i><span class="t">${esc(i.title)}</span>` +
      `${i.detail ? `<span class="d">${esc(i.detail)}</span>` : ""}<span class="s">${STATE[i.state] ? `<span class="st ${esc(i.state)}">${esc(STATE[i.state])}</span> · ` : ""}` +
      `${esc(i.source)}${i.due ? ` · due ${esc(day(i.due))}` : ""}</span></a></li>`).join("")
      : `<li class="todo-e">${d ? "Nothing needs a person right now." : "Could not load the list."}</li>`;
    panel.querySelector(".todo-h span").textContent = sum;
    panel.querySelector(".todo-l").innerHTML = html;
    if (cardList && d) {            // shown once the list has loaded, never as an error first
      if (cardList._h !== html) { cardList._h = html; cardList.innerHTML = html; }
      cardSum.textContent = sum || "What needs a person, across the platform";
      card.hidden = false;
    }
  }
  async function load() {
    try {
      const r = await fetch("/api/todo", {credentials: "same-origin"});
      if (r.status === 404) { slot.hidden = true; if (card) card.hidden = true; return; }   // not on this kind of server
      if (!r.ok) return paint(last);
      paint(await r.json());
    } catch (e) { paint(last); }
  }
  function open(on) {
    if (on) place();
    panel.classList.toggle("show", on);
    btn.setAttribute("aria-expanded", String(on));
    if (on) { load(); setTimeout(() => { const f = panel.querySelector(".todo-l a, .todo-x"); if (f) f.focus(); }, 40); }
  }
  btn.addEventListener("click", (e) => { e.stopPropagation(); open(!panel.classList.contains("show")); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && panel.classList.contains("show")) { e.stopPropagation(); open(false); btn.focus(); }
  }, true);
  // A click elsewhere closes it: it never stays over the dashboard.
  document.addEventListener("click", (e) => {
    if (panel.classList.contains("show") && !panel.contains(e.target) && !slot.contains(e.target)) open(false);
  });
  addEventListener("resize", () => { if (panel.classList.contains("show")) place(); });
  // Close, and follow the link even when it is where the page already is.
  panel.addEventListener("click", (e) => { const a = e.target.closest("a"); if (a) { open(false); follow(e, a); } });
  if (cardList) cardList.addEventListener("click", (e) => { const a = e.target.closest("a"); if (a) follow(e, a); });
  load();
  setInterval(() => { if (!document.hidden) load(); }, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  window.ctTodo = {reload: load};
})();
