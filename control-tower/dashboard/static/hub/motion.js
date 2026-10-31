/* The hub's motion: every action has its own movement, as Apple's apps do.
   Presentation only. It never changes data, an address or what a click does; it watches
   what the page already does and moves it. With "reduce motion" on, it does nothing.

   Action                         Movement
   menu item                      the yellow selection glides to it; the page rises (down the menu) or drops (up)
   page title                     the new title slides up into place
   a number that changes          it counts to its new value
   a number tile / exception row  the list sheet grows out of what was clicked, and shrinks back into it
   list rows                      cascade in once; "Show more" brings in only the new ones
   Globe | Map, theme             a thumb slides under the chosen option; the chosen globe or map settles in
   theme                          the new theme spreads out in a circle from the button pressed
   Ask ATLAS                      the chat opens in a circle from the button pressed
   Download CSV                   a check is drawn on the button
   Try Again                      the button spins while it retries
   To Do count                    the badge pops when its number changes
   first load                     the tiles and cards arrive one after the other
   new activity                   the new row slides in and glows once
   sidebar                        the names fade back in when it is maximised
   search results                 cascade in under the field
*/
(() => {
  "use strict";
  const RM = matchMedia("(prefers-reduced-motion: reduce)");
  const calm = () => RM.matches;
  const $ = (id) => document.getElementById(id);
  const EASE = "cubic-bezier(.32,.72,0,1)";          // Apple's sheet curve
  const SPRING = "cubic-bezier(.34,1.36,.64,1)";       // a little overshoot, then rest
  const anim = (el, kf, o) => (el && el.animate && !calm()) ? el.animate(kf, Object.assign({duration: 380, easing: EASE}, o)) : null;
  const NF = new Intl.NumberFormat("en-US");
  const watch = (el, fn, opts) => { if (el) new MutationObserver(fn).observe(el, opts || {childList: true, subtree: true}); };

  /* ── numbers count to their new value ── */
  const seen = new Map();
  const NUM = /^\d{1,3}(,\d{3})*$|^\d+$/;
  function countIn(root, sel) {
    root.querySelectorAll(sel).forEach((el, i) => {
      const txt = el.textContent.trim();
      if (el.children.length || !NUM.test(txt)) return;
      const to = Number(txt.replace(/,/g, "")), key = root.id + ":" + i;
      const from = seen.has(key) ? seen.get(key) : 0;
      seen.set(key, to);
      if (calm() || from === to) return;
      const t0 = performance.now(), d = Math.min(1000, 480 + 110 * Math.log10(Math.abs(to - from) + 1));
      const step = (t) => {
        if (!el.isConnected) return;
        const p = Math.min(1, (t - t0) / d), e = 1 - Math.pow(1 - p, 4);
        el.textContent = NF.format(Math.round(from + (to - from) * e));
        if (p < 1) requestAnimationFrame(step); else el.textContent = txt;
      };
      requestAnimationFrame(step);       // a hidden tab never runs it: the number stays as written
    });
  }
  const COUNTED = [["strip", ".chip .v, .chip .v span"], ["exL", "em"], ["runGrid", "b"]];
  for (const [id, sel] of COUNTED) { const el = $(id); if (el) { watch(el, () => countIn(el, sel), {childList: true}); countIn(el, sel); } }

  /* ── first load: tiles and cards arrive one after the other ── */
  let arrived = false;
  function arrive() {
    if (arrived || !$("strip") || !$("strip").querySelector(".chip")) return;
    arrived = true;
    const ov = document.querySelector('[data-view="overview"]');
    if (!ov || ov.hidden) return;
    [...ov.querySelectorAll(".chip, .card")].forEach((el, i) =>
      anim(el, [{opacity: 0, transform: "translateY(18px) scale(.98)"}, {opacity: 1, transform: "none"}], {duration: 620, delay: Math.min(i, 10) * 55, fill: "backwards"}));
  }
  watch($("strip"), arrive, {childList: true});

  /* ── the menu: the selection glides, the page rises or drops ── */
  const side = $("side"), nav = $("nav"), content = $("content");
  let navFrom = null;
  const relRect = (el) => {
    const r = el.getBoundingClientRect(), s = side.getBoundingClientRect();
    return {x: r.left - s.left + side.scrollLeft, y: r.top - s.top + side.scrollTop, w: r.width, h: r.height};
  };
  if (nav) nav.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-mod]"), cur = nav.querySelector('a[aria-current="page"]');
    if (!a || a === cur || e.defaultPrevented) return;
    navFrom = cur ? relRect(cur) : null;
    const all = [...nav.querySelectorAll("a[data-mod]")];
    // down the menu the page rises into place, up the menu it drops into place
    content.style.setProperty("--nav-dy", cur && all.indexOf(a) < all.indexOf(cur) ? "-18px" : "18px");
  }, true);
  function glide(f, to) {
    const t = relRect(to), ink = document.createElement("i");
    ink.className = "nav-ink"; ink.setAttribute("aria-hidden", "true");
    Object.assign(ink.style, {left: t.x + "px", top: t.y + "px", width: t.w + "px", height: t.h + "px"});
    side.appendChild(ink); to.classList.add("ink-wait");
    const a = ink.animate([{transform: `translate(${f.x - t.x}px,${f.y - t.y}px)`, width: f.w + "px", height: f.h + "px"},
      {transform: "none", width: t.w + "px", height: t.h + "px"}], {duration: 460, easing: SPRING});
    const done = () => { ink.remove(); to.classList.remove("ink-wait"); anim(to.querySelector("svg"), [{transform: "scale(.7)"}, {transform: "none"}], {duration: 360, easing: SPRING}); };
    a.onfinish = done; a.oncancel = done;
  }
  addEventListener("hashchange", () => {
    const to = nav && nav.querySelector('a[aria-current="page"]');
    if (navFrom && to && !calm()) glide(navFrom, to);
    navFrom = null;
    setTimeout(() => content.style.removeProperty("--nav-dy"), 700);   // a link elsewhere: the plain rise
    // ATA's own tabs: the chosen one pops
    const tab = document.querySelector('#ataTabs:not([hidden]) a[aria-current="page"]');
    anim(tab, [{transform: "scale(.88)", opacity: .6}, {transform: "none", opacity: 1}], {duration: 420, easing: SPRING});
  });

  /* ── the page title slides up into place ── */
  const title = $("title");
  let titleWas = title ? title.textContent : "";
  watch(title, () => {
    if (title.textContent === titleWas) return;
    titleWas = title.textContent;
    anim(title, [{opacity: 0, transform: "translateY(8px)", filter: "blur(2px)"}, {opacity: 1, transform: "none", filter: "none"}], {duration: 340});
  }, {childList: true, characterData: true, subtree: true});

  /* ── what was clicked last: the sheet and the chat open out of it ── */
  let origin = null, originAt = 0;
  addEventListener("click", (e) => {
    const el = e.target.closest && e.target.closest("button, a, [role=button]");
    if (el) { origin = el; originAt = performance.now(); }
  }, true);
  const fromClick = () => (origin && origin.isConnected && performance.now() - originAt < 1500) ? origin.getBoundingClientRect() : null;

  /* ── the list sheet grows out of the tile or row, and shrinks back into it ── */
  const dl = $("dl"), dlb = dl && dl.querySelector(".dl-b"), rows = $("dlRows");
  let sheetFrom = null, cascade = false, moreFrom = -1;
  const insetFor = (o) => {
    const b = dlb.getBoundingClientRect();
    const v = [o.top - b.top, b.right - o.right, b.bottom - o.bottom, o.left - b.left].map((x) => Math.max(0, Math.round(x)));
    return `inset(${v[0]}px ${v[1]}px ${v[2]}px ${v[3]}px round 16px)`;
  };
  if (dl) new MutationObserver(() => {
    const on = dl.classList.contains("on");
    if (on && !dl.__on) {
      dl.__on = true; cascade = true;
      dlb.getAnimations().forEach((a) => a.cancel());
      sheetFrom = fromClick();
      if (sheetFrom) anim(dlb, [{clipPath: insetFor(sheetFrom), opacity: .5}, {clipPath: "inset(0px 0px 0px 0px round 18px)", opacity: 1}], {duration: 520});
      else anim(dlb, [{opacity: 0, transform: "translateY(24px) scale(.96)"}, {opacity: 1, transform: "none"}], {duration: 420});
    } else if (!on && dl.__on) {
      dl.__on = false;
      const back = sheetFrom && origin && origin.isConnected ? origin.getBoundingClientRect() : null;
      anim(dlb, back ? [{clipPath: "inset(0px 0px 0px 0px round 18px)", opacity: 1}, {clipPath: insetFor(back), opacity: 0}]
        : [{opacity: 1, transform: "none"}, {opacity: 0, transform: "scale(.96)"}], {duration: 220, easing: "cubic-bezier(.4,0,1,1)", fill: "forwards"});
    }
  }).observe(dl, {attributes: true, attributeFilter: ["class"]});
  const more = $("dlMore");
  if (more) more.addEventListener("click", () => { moreFrom = rows.children.length; }, true);
  watch(rows, () => {
    const tr = [...rows.children];
    if (cascade && tr.length > 1) {
      cascade = false;
      tr.slice(0, 16).forEach((r, i) => anim(r, [{opacity: 0, transform: "translateY(8px)"}, {opacity: 1, transform: "none"}], {duration: 360, delay: 120 + i * 22, fill: "backwards"}));
    } else if (moreFrom >= 0) {
      tr.slice(moreFrom, moreFrom + 30).forEach((r, i) => anim(r, [{opacity: 0, transform: "translateY(10px)"}, {opacity: 1, transform: "none"}], {duration: 340, delay: i * 14, fill: "backwards"}));
      moreFrom = -1;
    }
  }, {childList: true});

  /* ── Download CSV: a check is drawn on the button ── */
  const CHECK = '<svg class="mo-ok" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  const csv = $("dlCsv");
  if (csv) csv.addEventListener("click", () => {
    if (calm() || csv.querySelector(".mo-ok")) return;
    csv.classList.add("mo-done"); csv.insertAdjacentHTML("afterbegin", CHECK);
    setTimeout(() => { csv.classList.remove("mo-done"); const s = csv.querySelector(".mo-ok"); if (s) s.remove(); }, 1600);
  });

  /* ── Try Again spins while it retries ── */
  addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-retry]");
    if (b && !calm()) b.classList.add("mo-busy");
  }, true);

  /* ── segmented controls: a thumb slides under the chosen option ── */
  function thumb(group) {
    if (!group || group.__thumb) return;
    const t = document.createElement("i");
    t.className = "seg-thumb"; t.setAttribute("aria-hidden", "true");
    group.prepend(t); group.classList.add("has-thumb"); group.__thumb = t;
    let ready = false;
    const place = () => {
      const b = group.querySelector('button[aria-pressed="true"]');
      if (!b || !b.offsetWidth) { t.style.opacity = "0"; return; }
      Object.assign(t.style, {opacity: "1", left: b.offsetLeft + "px", top: b.offsetTop + "px", width: b.offsetWidth + "px", height: b.offsetHeight + "px"});
      if (!ready) requestAnimationFrame(() => { ready = true; t.classList.add("live"); });
    };
    new MutationObserver(place).observe(group, {attributes: true, subtree: true, attributeFilter: ["aria-pressed"]});
    if (window.ResizeObserver) new ResizeObserver(place).observe(group);
    place();
  }
  thumb($("mapSeg")); thumb($("theme"));

  /* ── the chosen globe or map settles in ── */
  for (const id of ["globe", "map"]) {
    const box = $(id);
    if (box) new MutationObserver(() => {
      if (!box.hidden) anim(box, [{opacity: 0, transform: "scale(.97)"}, {opacity: 1, transform: "none"}], {duration: 480});
    }).observe(box, {attributes: true, attributeFilter: ["hidden"]});
  }

  /* ── the theme spreads out in a circle from the button pressed ── */
  function themeSwap(btn, go) {
    if (calm() || !document.startViewTransition || !btn) return go();
    const r = btn.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
    const R = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    document.documentElement.classList.add("mo-theme");
    const vt = document.startViewTransition(go);
    vt.ready.then(() => document.documentElement.animate({clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${R}px at ${x}px ${y}px)`]},
      {duration: 620, easing: EASE, pseudoElement: "::view-transition-new(root)"})).catch(() => {});
    vt.finished.finally(() => document.documentElement.classList.remove("mo-theme"));
  }

  /* ── Ask ATLAS: the chat opens in a circle from the button pressed ── */
  const chat = $("chatF");
  if (chat) new MutationObserver(() => {
    if (chat.hidden) return;
    const o = fromClick();
    const x = o ? o.left + o.width / 2 : innerWidth - 40, y = o ? o.top + o.height / 2 : innerHeight - 40;
    const R = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    anim(chat, {clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${R}px at ${x}px ${y}px)`]}, {duration: 560});
  }).observe(chat, {attributes: true, attributeFilter: ["hidden"]});

  /* ── the To Do badge pops when its number changes ── */
  const slot = document.querySelector("[data-todo-slot]");
  let badgeWas = null;
  watch(slot, () => {
    const n = slot.querySelector(".todo-n");
    if (!n || n.hidden || n.textContent === badgeWas) { if (n) badgeWas = n.hidden ? null : n.textContent; return; }
    const first = badgeWas === null && !n.__seen; n.__seen = true; badgeWas = n.textContent;
    anim(n, [{transform: "scale(.3)"}, {transform: "scale(1.25)", offset: .55}, {transform: "none"}], {duration: first ? 520 : 460, easing: "ease-out"});
  }, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["hidden"]});

  /* ── new activity slides in and glows once ── */
  const act = $("actL");
  let actSeen = null;
  watch(act, () => {
    const keys = [...act.children].map((li) => li.textContent);
    if (actSeen) [...act.children].forEach((li, i) => {
      if (!actSeen.has(keys[i]) && li.querySelector("a")) {
        anim(li, [{opacity: 0, transform: "translateX(-14px)"}, {opacity: 1, transform: "none"}], {duration: 460, easing: SPRING});
        li.classList.add("mo-new"); setTimeout(() => li.classList.remove("mo-new"), 2400);
      }
    });
    actSeen = new Set(keys);
  }, {childList: true});

  /* ── the sidebar: the names fade back in when it is maximised ── */
  const sideT = $("sideT");
  if (sideT) sideT.addEventListener("click", () => {
    anim(sideT.querySelector("svg"), [{transform: "scaleX(-1)"}, {transform: "none"}], {duration: 420, easing: SPRING});
    if (document.documentElement.classList.contains("side-mini")) return;
    side.querySelectorAll(".nav a > span:not(.st), .grp, .brand > span:last-child, .corp img").forEach((el, i) =>
      anim(el, [{opacity: 0, transform: "translateX(-8px)"}, {opacity: 1, transform: "none"}], {duration: 380, delay: 80 + Math.min(i, 14) * 14, fill: "backwards"}));
  });

  /* ── search results cascade in under the field ── */
  const srch = document.querySelector("[data-search-slot]");
  let srchWired = false;
  watch(srch, () => {
    const r = srch.querySelector(".srch-r");
    if (!r || srchWired) return;
    srchWired = true;
    watch(r, () => [...r.children].slice(0, 12).forEach((el, i) =>
      anim(el, [{opacity: 0, transform: "translateY(6px)"}, {opacity: 1, transform: "none"}], {duration: 300, delay: i * 24, fill: "backwards"})), {childList: true});
  });

  window.HubMotion = {theme: themeSwap, countIn};
})();
