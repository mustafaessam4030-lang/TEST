// LumaMascot: Luma's animated mascot and its state machine.
//
// One SVG drawing (luma-mascot.svg) is loaded once and cloned for each live mascot; a state is
// shown by setting data-state on the clone (props, expression and motion live in app.css).
// Static copies (beside finished answers, in the sidebar) are plain <img> of the same file, so
// they never animate. A mascot that fails to load falls back to that image.
//
// States: idle, waiting, thinking, searching, reading, analyzing, writing, done, error, static.
// The app maps real request events to states (see app.js, PHASE_STATE); nothing here runs a
// timer that changes what the assistant appears to be doing, except "done", which plays once
// and then settles into its resting state.

export const MASCOT_SRC = "luma/luma-mascot.svg";
export const STATES = ["idle", "waiting", "thinking", "searching", "reading", "analyzing", "writing", "done",
  "error", "static"];

let template = null;   // parsed <svg>, or false when it could not be loaded
let loading = null;
let serial = 0;

export function preloadMascot() {
  if (!loading) {
    loading = fetch(MASCOT_SRC)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error("HTTP " + r.status))))
      .then((text) => {
        const doc = new DOMParser().parseFromString(text, "image/svg+xml");
        const svg = doc.documentElement;
        template = svg && svg.nodeName === "svg" && !doc.querySelector("parsererror") ? svg : false;
      })
      .catch(() => { template = false; });
  }
  return loading;
}

// An inline copy with ids made unique, so copies never resolve each other's gradients
// (a hidden copy's gradient would otherwise render nothing).
function cloneSvg() {
  const svg = document.importNode(template, true);
  const n = ++serial;
  const ids = {};
  svg.querySelectorAll("[id]").forEach((el) => { ids[el.id] = el.id + "-" + n; el.id = ids[el.id]; });
  svg.querySelectorAll("*").forEach((el) => {
    for (const attr of ["fill", "stroke", "clip-path", "mask", "filter"]) {
      const v = el.getAttribute(attr);
      if (v && v.startsWith("url(#")) el.setAttribute(attr, v.replace(/url\(#([^)]+)\)/, (_, id) => `url(#${ids[id] || id})`));
    }
  });
  svg.removeAttribute("role");
  svg.removeAttribute("aria-label");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  return svg;
}

// Pause looping animation while a mascot is scrolled out of view.
const watcher = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
  entries.forEach((e) => { e.target.toggleAttribute("data-paused", !e.isIntersecting); });
}) : null;

export function staticMascot(size = 28, cls = "") {
  const wrap = document.createElement("span");
  wrap.className = "luma luma-static " + cls;
  wrap.style.setProperty("--ls", size + "px");
  wrap.setAttribute("aria-hidden", "true");
  const img = document.createElement("img");
  img.src = MASCOT_SRC;
  img.alt = "";
  img.decoding = "async";
  wrap.append(img);
  return wrap;
}

export class LumaMascot {
  constructor({ size = 36, state = "idle", rest = "static", cls = "" } = {}) {
    this.el = document.createElement("span");
    this.el.className = "luma luma-live " + cls;
    this.el.style.setProperty("--ls", size + "px");
    this.el.setAttribute("aria-hidden", "true");     // decorative: the status text says what is happening
    this.rest = rest;                                // the state "done" settles into
    this.token = 0;
    this.svg = null;
    this.state = null;
    this._render();
    this.set(state);
    if (watcher) watcher.observe(this.el);
  }
  _render() {
    if (template) {
      this.svg = cloneSvg();
      this.el.replaceChildren(this.svg);
    } else {
      const img = document.createElement("img");
      img.src = MASCOT_SRC;
      img.alt = "";
      this.el.replaceChildren(img);
      if (template === null) preloadMascot().then(() => { if (template && !this.dead) { this._render(); this.set(this.state, true); } });
    }
  }
  set(state, force) {
    if (this.dead || !STATES.includes(state) || (state === this.state && !force)) return this;
    const token = ++this.token;                     // any newer state cancels a pending settle
    this.state = state;
    this.el.dataset.state = state;
    if (this.svg) this.svg.dataset.state = state;
    if (state === "done") {
      setTimeout(() => { if (token === this.token && !this.dead) this.set(this.rest); }, 950);
    }
    return this;
  }
  size(px) { this.el.style.setProperty("--ls", px + "px"); return this; }
  destroy() {
    this.dead = true;
    this.token++;
    if (watcher) watcher.unobserve(this.el);
  }
}
