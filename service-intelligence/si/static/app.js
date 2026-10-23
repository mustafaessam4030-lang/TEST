"use strict";
/* Service Intelligence UI. All data comes from the server API; this file holds no credentials
   and never talks to Snowflake. Every value rendered from the API goes through textContent. */

const S = {
  me: null, meta: null, metric: null, dimension: null, filters: {}, start: null, end: null,
  conversation: null, candidates: [], drill: null, inflight: new Map(),
};
const $ = (id) => document.getElementById(id);

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const svgEl = (tag, attrs) => {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
  return el;
};

/* ---------- formatting ---------- */
function fmt(v, unit) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (unit === "ratio") return (v * 100).toFixed(1) + "%";
  if (unit === "hours") return v >= 100 ? Math.round(v).toLocaleString() + " h" : v.toFixed(1) + " h";
  if (unit === "days") return v.toFixed(1) + " d";
  if (unit === "count") return Math.round(v).toLocaleString();
  return typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v);
}
function compact(v, unit) {
  if (unit === "ratio") return Math.round(v * 100) + "%";
  if (v === 0) return "0";
  if (Math.abs(v) >= 1000) return (v / 1000).toFixed(v >= 10000 ? 0 : 1) + "K";
  return unit === "count" ? String(Math.round(v)) : v.toFixed(v < 10 ? 1 : 0);
}
const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (s, n) => { const d = new Date(s + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return iso(d); };

/* ---------- API ---------- */
function token() { try { return sessionStorage.getItem("si-token"); } catch { return null; } }
async function api(path, opts = {}) {
  const headers = { "Accept": "application/json" };
  if (opts.body) headers["Content-Type"] = "application/json";
  const t = token();
  if (t) headers["Authorization"] = "Bearer " + t;
  const key = (opts.method || "GET") + " " + path + " " + (opts.body ? JSON.stringify(opts.body) : "");
  if (!opts.body && S.inflight.has(key)) return S.inflight.get(key);   // de-duplicate identical reads
  const p = fetch(path, { method: opts.method || "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined,
                          credentials: "same-origin" })
    .then(async (r) => {
      let data = null;
      try { data = await r.json(); } catch { data = null; }
      if (!r.ok) {
        const err = new Error((data && (data.message || data.detail)) || ("Request failed (" + r.status + ")"));
        err.status = r.status; err.kind = data && data.error; throw err;
      }
      return data;
    })
    .finally(() => S.inflight.delete(key));
  if (!opts.body) S.inflight.set(key, p);
  return p;
}
function qs(extra) {
  const p = new URLSearchParams();
  if (S.start) p.set("start", S.start);
  if (S.end) p.set("end", S.end);
  const f = Object.fromEntries(Object.entries(S.filters).filter(([, v]) => v && v.length));
  if (Object.keys(f).length) p.set("filters", JSON.stringify(f));
  for (const [k, v] of Object.entries(extra || {})) if (v !== null && v !== undefined) p.set(k, v);
  return p.toString();
}
function bodyCtx() {
  const f = Object.fromEntries(Object.entries(S.filters).filter(([, v]) => v && v.length));
  return { start: S.start, end: S.end, filters: f };
}
function errorState(el, err) {
  el.replaceChildren(h("div", { class: "state error", role: "alert" },
    err.status === 403 ? "You do not have permission for this view." :
    err.kind === "warehouse_unavailable" ? "The Snowflake warehouse is unavailable or suspended. Try again shortly." :
    err.kind === "timeout" ? "The query took too long and was cancelled. Narrow the period or filters." :
    err.kind === "permission_denied" ? "The application's Snowflake role is not authorised for this data." :
    err.message));
}
const loading = (el, what) => el.replaceChildren(h("div", { class: "state" }, "Loading " + (what || "") + "…"));

/* ---------- tooltip ---------- */
const tip = { show(evt, text) { const t = $("tooltip"); t.textContent = text; t.hidden = false;
  t.style.left = Math.min(evt.clientX + 12, window.innerWidth - 270) + "px"; t.style.top = (evt.clientY + 12) + "px"; },
  hide() { $("tooltip").hidden = true; } };

/* ---------- charts (single series: one hue, thin marks, recessive grid) ---------- */
function niceMax(v) { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); const m = v / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p; }

function barChart(container, title, rows, unit, minSample, onPick) {
  container.replaceChildren();
  if (!rows.length) { container.append(h("div", { class: "state" }, "No records match this breakdown.")); return; }
  const W = 640, labelW = 150, valueW = 70, rowH = 26, bar = 16;
  const max = niceMax(Math.max(...rows.map((r) => r.value || 0)));
  const plotW = W - labelW - valueW;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${rows.length * rowH + 22}`, role: "img", "aria-label": title });
  const grid = svgEl("g", { class: "grid" });
  for (let i = 0; i <= 4; i++) {
    const x = labelW + (plotW * i) / 4;
    grid.append(svgEl("line", { x1: x, x2: x, y1: 0, y2: rows.length * rowH }));
    const t = svgEl("text", { x, y: rows.length * rowH + 14, "text-anchor": "middle" }); t.textContent = compact((max * i) / 4, unit);
    grid.append(t);
  }
  svg.append(grid);
  rows.forEach((r, i) => {
    const y = i * rowH + (rowH - bar) / 2, w = Math.max(1, (plotW * (r.value || 0)) / max);
    const lab = svgEl("text", { x: labelW - 8, y: y + bar - 4, "text-anchor": "end" });
    lab.textContent = r.segment.length > 22 ? r.segment.slice(0, 21) + "…" : r.segment;
    const small = minSample && r.n < minSample;
    const path = svgEl("path", { class: "bar" + (small ? " small-n" : ""), tabindex: 0, role: "button",
      "aria-label": `${r.segment}: ${fmt(r.value, unit)}, ${r.n} records${small ? ", too few records" : ""}. Show records.`,
      d: `M${labelW},${y} h${Math.max(0, w - 4)} a4,4 0 0 1 4,4 v${bar - 8} a4,4 0 0 1 -4,4 h${-Math.max(0, w - 4)} z` });
    const text = `${r.segment}: ${fmt(r.value, unit)} · ${r.n.toLocaleString()} records${small ? " · too few records" : ""}`;
    path.addEventListener("mousemove", (e) => tip.show(e, text));
    path.addEventListener("mouseleave", tip.hide);
    path.addEventListener("click", () => onPick && onPick(r));
    path.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick && onPick(r); } });
    const val = svgEl("text", { x: labelW + w + 6, y: y + bar - 4, class: i === 0 ? "label-strong" : "" });
    val.textContent = fmt(r.value, unit);
    svg.append(lab, path, val);
  });
  container.append(h("h4", {}, title), svg,
    h("p", { class: "muted small" }, "Select a bar to see its records. Outlined bars have too few records to rank."));
}

function lineChart(container, title, points, unit) {
  container.replaceChildren();
  if (!points.length) { container.append(h("div", { class: "state" }, "No records in this period.")); return; }
  const W = 640, H = 220, L = 46, R = 12, T = 10, B = 26;
  const vals = points.map((p) => p.y ?? 0);
  const max = niceMax(Math.max(...vals)), n = points.length;
  const x = (i) => L + (n === 1 ? (W - L - R) / 2 : ((W - L - R) * i) / (n - 1));
  const y = (v) => T + (H - T - B) * (1 - (v ?? 0) / max);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title });
  const grid = svgEl("g", { class: "grid" });
  for (let i = 0; i <= 4; i++) {
    const yy = T + ((H - T - B) * i) / 4;
    grid.append(svgEl("line", { x1: L, x2: W - R, y1: yy, y2: yy }));
    const t = svgEl("text", { x: L - 6, y: yy + 4, "text-anchor": "end" }); t.textContent = compact(max * (1 - i / 4), unit);
    grid.append(t);
  }
  const step = Math.max(1, Math.ceil(n / 6));
  points.forEach((p, i) => { if (i % step === 0 || i === n - 1) {
    const t = svgEl("text", { x: x(i), y: H - 6, "text-anchor": "middle" }); t.textContent = p.x.slice(5); grid.append(t); } });
  svg.append(grid);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.y)}`).join(" ");
  svg.append(svgEl("path", { class: "area", d: `${d} L${x(n - 1)},${y(0)} L${x(0)},${y(0)} Z` }));
  svg.append(svgEl("path", { class: "line", d }));
  points.forEach((p, i) => svg.append(svgEl("circle", { class: "dot" + (p.sufficient === false || p.partial ? " small-n" : ""), cx: x(i), cy: y(p.y), r: 4 })));
  const last = points[n - 1];
  const endLab = svgEl("text", { x: x(n - 1), y: y(last.y) - 10, "text-anchor": "end", class: "label-strong" });
  endLab.textContent = fmt(last.y, unit);
  svg.append(endLab);
  const hit = svgEl("rect", { class: "hit", x: L, y: T, width: W - L - R, height: H - T - B });
  hit.addEventListener("mousemove", (e) => {
    const box = svg.getBoundingClientRect(); const rel = ((e.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((rel - L) / (W - L - R)) * (n - 1))));
    const p = points[i];
    tip.show(e, `${p.x}: ${fmt(p.y, unit)} · ${p.n} records${p.partial ? " · partial period" : ""}${p.sufficient === false ? " · too few records" : ""}`);
  });
  hit.addEventListener("mouseleave", tip.hide);
  svg.append(hit);
  container.append(h("h4", {}, title), svg,
    h("p", { class: "muted small" }, "Hollow points cover a partial period or have too few records to be reliable."));
}

function table(columns, rows, caption) {
  return h("div", { class: "table-wrap" }, h("table", {},
    caption ? h("caption", {}, caption) : null,
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { scope: "col", class: c.unit ? "num" : "" }, c.label)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, columns.map((c) => {
      const v = r[c.key];
      const unit = c.unit || (c.key === "value" || c.key === "previous" ? r.unit : null);
      return h("td", { class: unit ? "num" : "" }, typeof v === "number" ? fmt(v, unit) : v === null || v === undefined ? "—" : String(v));
    }))))));
}

function sourcesBlock(sources) {
  if (!sources || !sources.length) return null;
  return h("details", {}, h("summary", {}, `Sources (${sources.length} quer${sources.length === 1 ? "y" : "ies"})`),
    sources.map((s) => h("div", {},
      h("p", { class: "small" }, s.label, " — query id ", h("span", { class: "mono" }, s.query_id || "n/a"),
        s.cached ? " (cached)" : ""),
      s.sql ? h("pre", { class: "sql mono" }, s.sql + (s.params && s.params.length ? "\n-- bind values: " + JSON.stringify(s.params) : "")) : null)));
}

/* ---------- shell: freshness, filters, routing ---------- */
function renderShell() {
  const m = S.meta, f = m.freshness;
  const fr = $("freshness");
  fr.textContent = f.as_of ? `Data as of ${f.as_of.replace("T", " ").slice(0, 16)} UTC (${f.basis})` +
    (f.stale ? ` — older than ${f.max_age_hours} h` : "") + ` · times in ${m.reporting_tz}` : "No data refresh time available";
  fr.classList.toggle("stale", !!f.stale);
  const b = $("source-banner");
  if (m.data_source.synthetic) { b.hidden = false; b.textContent = m.data_source.label + ". Figures are for development and demonstration only."; }
  const scope = Object.entries(S.me.scope || {}).map(([k, v]) => `${k}: ${v.join(", ")}`).join("; ");
  $("who").replaceChildren(h("div", {}, S.me.name), h("div", { class: "muted small" }, S.me.roles.join(", ") + (scope ? ` · scope ${scope}` : "")));
  const dims = $("dim-filters");
  dims.replaceChildren();
  for (const d of m.dimensions) {
    if (!d.available) continue;
    const sel = h("select", { id: "dim-" + d.id, "data-dim": d.id },
      h("option", { value: "" }, "All"),
      d.values.map((v) => h("option", { value: v.value }, `${v.value} (${v.n.toLocaleString()})`)));
    sel.value = (S.filters[d.id] || [""])[0];
    dims.append(h("div", { class: "field" }, h("label", { for: "dim-" + d.id }, d.label), sel));
  }
  $("f-start").value = S.start; $("f-end").value = S.end;
  if (m.first_created) $("f-start").min = m.first_created.slice(0, 10);
  const dsel = $("dimension");
  dsel.replaceChildren(...m.dimensions.filter((d) => d.available).map((d) => h("option", { value: d.id }, d.label)));
  if (S.dimension) dsel.value = S.dimension; else S.dimension = dsel.value;
}

function readFilters() {
  S.start = $("f-start").value || S.meta.default_period.start;
  S.end = $("f-end").value || S.meta.default_period.end;
  if (S.end < S.start) { const t = S.start; S.start = S.end; S.end = t; }
  S.filters = {};
  document.querySelectorAll("[data-dim]").forEach((s) => { if (s.value) S.filters[s.dataset.dim] = [s.value]; });
  try { sessionStorage.setItem("si-view", JSON.stringify({ start: S.start, end: S.end, filters: S.filters })); } catch { /* optional */ }
}

function preset(kind) {
  const last = S.meta.default_period.end;
  if (kind === "30" || kind === "90") { S.end = last; S.start = addDays(last, -(Number(kind) - 1)); }
  else if (kind === "month") { S.end = last; S.start = last.slice(0, 8) + "01"; }
  else if (kind === "lastmonth") { const first = last.slice(0, 8) + "01"; S.end = addDays(first, -1); S.start = S.end.slice(0, 8) + "01"; }
  $("f-start").value = S.start; $("f-end").value = S.end;
}

function route() {
  if (!S.me) return;
  const page = (location.hash.replace("#/", "") || "analysis").split("?")[0];
  const pages = ["analysis", "recommendations", "assistant"];
  const current = pages.includes(page) ? page : "analysis";
  for (const p of pages) $("page-" + p).hidden = p !== current;
  $("page-signin").hidden = true;
  document.querySelectorAll(".tabs a").forEach((a) => { if (a.dataset.page === current) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  if (current === "analysis") loadAnalysis();
  if (current === "recommendations") loadPlan();
  if (current === "assistant") renderExamples();
}

/* ---------- Page 1: analysis ---------- */
let analysisKey = null;
async function loadAnalysis(force) {
  const key = qs();
  if (!force && key === analysisKey && $("kpis").childElementCount) return;
  analysisKey = key;
  loading($("kpis"), "metrics");
  try {
    const data = await api("/api/overview?" + key);
    const pl = $("period-line");
    pl.replaceChildren(`Reporting period ${data.period.label} (${S.meta.reporting_tz}). Current-state metrics are as of the last data refresh.`,
      ...data.period.notes.map((n) => h("span", { class: "note" }, n)));
    renderKpis(data.kpis);
    renderQuality(data.quality);
    const available = data.kpis.filter((k) => k.available);
    if (!S.metric || !available.find((k) => k.metric.id === S.metric)) S.metric = available.length ? available[0].metric.id : null;
    selectMetric(S.metric, data.kpis);
  } catch (e) { errorState($("kpis"), e); }
}

function deltaNode(k) {
  if (!k.previous) return null;
  if (!k.change) return h("span", { class: "delta flat" }, "No reliable comparison");
  const up = k.change.abs > 0, dir = k.metric.direction;
  const good = dir === "neutral" || k.change.abs === 0 ? null : (up === (dir === "higher_is_better"));
  const cls = good === null ? "flat" : good ? "good" : "bad";
  const sign = up ? "▲" : k.change.abs < 0 ? "▼" : "■";
  const amt = k.metric.unit === "ratio" ? `${(k.change.abs * 100).toFixed(1)} pts` :
    k.change.pct !== null ? `${(k.change.pct * 100).toFixed(1)}%` : fmt(k.change.abs, k.metric.unit);
  return h("span", { class: "delta " + cls }, `${sign} ${amt} vs previous period` + (good === null ? "" : good ? " (better)" : " (worse)"));
}

function renderKpis(kpis) {
  $("kpis").replaceChildren(...kpis.map((k) => {
    if (!k.available) {
      return h("div", { class: "kpi unavailable" }, h("span", { class: "label" }, k.metric.name),
        h("span", { class: "value" }, "Not available"), h("span", { class: "meta" }, k.reason));
    }
    return h("button", { type: "button", class: "kpi", "aria-pressed": String(k.metric.id === S.metric),
      onclick: () => selectMetric(k.metric.id, kpis) },
      h("span", { class: "label" }, k.metric.name),
      h("span", { class: "value" }, k.n === 0 ? "No data" : fmt(k.value, k.metric.unit)),
      deltaNode(k),
      h("span", { class: "meta" }, k.metric.kind === "snapshot" ? `Current · ${k.n.toLocaleString()} records`
        : `${k.n.toLocaleString()} records`, " ", k.sufficient === false ? h("span", { class: "tag warn" }, "Too few records") : null));
  }));
}

async function selectMetric(id, kpis) {
  S.metric = id;
  document.querySelectorAll(".kpi[aria-pressed]").forEach((b) => b.setAttribute("aria-pressed", "false"));
  const k = (kpis || []).find((x) => x.metric.id === id);
  document.querySelectorAll("#kpis .kpi").forEach((el) => {
    if (el.tagName === "BUTTON" && el.querySelector(".label").textContent === (k && k.metric.name)) el.setAttribute("aria-pressed", "true");
  });
  if (!k) { $("detail-title").textContent = "No metric available"; return; }
  const m = k.metric;
  $("detail-title").textContent = m.name;
  $("grain").disabled = m.kind !== "period";
  $("definition").replaceChildren(
    h("p", { class: "muted" }, m.description, m.kind === "snapshot" ? ` As of ${S.meta.as_of.replace("T", " ").slice(0, 16)} UTC.` : ""),
    h("details", {}, h("summary", {}, "How it is calculated"),
      h("dl", { class: "kv" },
        h("dt", {}, "Calculation"), h("dd", { class: "mono" }, m.expression),
        h("dt", {}, "Records included"), h("dd", { class: "mono" }, m.filter || "all"),
        m.time_column ? [h("dt", {}, "Counted by"), h("dd", {}, m.time_column + " in the reporting period")] : null,
        m.min_sample ? [h("dt", {}, "Minimum sample"), h("dd", {}, `${m.min_sample} records`)] : null,
        h("dt", {}, "Limitations"), h("dd", {}, h("ul", { class: "issues" }, m.limitations.map((l) => h("li", {}, l)))),
        h("dt", {}, "Registry"), h("dd", {}, S.meta.registry_version)),
      sourcesBlock([{ label: "This figure", ...k.evidence }].concat(k.previous ? [{ label: "Previous period", ...k.previous.evidence }] : []))),
    h("button", { type: "button", class: "link", onclick: () => openDrill(m.id, {}, m.name) }, "Show the records behind this figure"));
  loadTrend(); loadBreakdown();
}

async function loadTrend() {
  const m = S.meta.metrics.find((x) => x.id === S.metric);
  const el = $("trend");
  if (!m || m.kind !== "period") { el.replaceChildren(h("p", { class: "muted small" }, "Current-state metric: there is no history to trend because status history is not available.")); return; }
  loading(el, "trend");
  try {
    const d = await api("/api/series?" + qs({ metric: m.id, grain: $("grain").value }));
    lineChart(el, `${m.name} by ${d.grain}`, d.points.map((p) => ({ x: p.bucket.slice(0, 10), y: p.value, n: p.n, sufficient: p.sufficient, partial: p.partial })), m.unit);
  } catch (e) { errorState(el, e); }
}

async function loadBreakdown() {
  const m = S.meta.metrics.find((x) => x.id === S.metric);
  const el = $("breakdown");
  S.dimension = $("dimension").value;
  if (!m || !S.dimension) { el.replaceChildren(); return; }
  loading(el, "breakdown");
  try {
    const d = await api("/api/breakdown?" + qs({ metric: m.id, dimension: S.dimension }));
    const rows = d.rows.filter((r) => r.value !== null).sort((a, b) => b.value - a.value);
    barChart(el, `${m.name} by ${d.dimension_label.toLowerCase()}`, rows, m.unit, m.min_sample,
      (r) => openDrill(m.id, { [S.dimension]: [r.segment] }, `${m.name} — ${d.dimension_label}: ${r.segment}`));
    el.append(h("details", {}, h("summary", {}, "View as table"),
      table([{ key: "segment", label: d.dimension_label }, { key: "value", label: m.name, unit: m.unit }, { key: "n", label: "Records", unit: "count" }], rows)),
      sourcesBlock([{ label: "Breakdown", ...d.evidence }]));
  } catch (e) { errorState(el, e); }
}

async function openDrill(metricId, extra, title, offset = 0) {
  if (!S.me.permissions.includes("analysis.drilldown")) {
    $("drill-panel").hidden = false; $("drill-title").textContent = title;
    $("drill").replaceChildren(h("div", { class: "state" }, "Your role does not include record-level drill-down.")); return;
  }
  S.drill = { metricId, extra, title };
  if (location.hash !== "#/analysis") location.hash = "#/analysis";
  const panel = $("drill-panel"); panel.hidden = false; $("drill-title").textContent = title;
  const el = $("drill"); loading(el, "records");
  const filters = { ...Object.fromEntries(Object.entries(S.filters).filter(([, v]) => v && v.length)), ...extra };
  const p = new URLSearchParams({ metric: metricId, limit: 50, offset });
  if (S.start) p.set("start", S.start); if (S.end) p.set("end", S.end);
  if (Object.keys(filters).length) p.set("filters", JSON.stringify(filters));
  try {
    const d = await api("/api/drilldown?" + p.toString());
    if (!d.rows.length) { el.replaceChildren(h("div", { class: "state" }, "No records match.")); return; }
    el.replaceChildren(
      table(d.columns.map((c) => ({ key: c, label: c.replace(/_/g, " ") })), d.rows.map((r) => {
        const o = {}; for (const c of d.columns) o[c] = typeof r[c] === "string" && /T\d\d:/.test(r[c]) ? r[c].replace("T", " ").slice(0, 16) : r[c]; return o; }),
        `Records ${offset + 1}–${offset + d.rows.length}${d.has_more ? " (more available)" : ""}. Times in UTC. Only approved service fields are shown.`),
      h("div", { class: "plan-actions" },
        offset > 0 ? h("button", { type: "button", onclick: () => openDrill(metricId, extra, title, Math.max(0, offset - 50)) }, "Previous") : null,
        d.has_more ? h("button", { type: "button", onclick: () => openDrill(metricId, extra, title, offset + 50) }, "Next") : null),
      sourcesBlock([{ label: "Drill-down", ...d.evidence }]));
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { errorState(el, e); }
}

function renderQuality(q) {
  const comp = q.completeness.map((c) => {
    const bar = h("div", { class: "meter" }, h("span", {}));
    if (c.share !== null && c.share !== undefined) bar.firstChild.style.width = Math.round(c.share * 100) + "%";
    return { column: c.column, mapped: c.mapped ? "Yes" : "Not mapped", filled: c.share === null || c.share === undefined ? "—" : (c.share * 100).toFixed(1) + "%", bar };
  });
  $("quality").replaceChildren(
    h("p", { class: "small" }, `${q.total_rows.toLocaleString()} records from ${(q.first_created || "?").slice(0, 10)} to ${(q.last_created || "?").slice(0, 10)}.`),
    h("div", { class: "table-wrap" }, h("table", {}, h("caption", {}, "Completeness of contract fields"),
      h("thead", {}, h("tr", {}, h("th", {}, "Field"), h("th", {}, "Mapped"), h("th", { class: "num" }, "Filled"), h("th", {}, h("span", { class: "sr-only" }, "Bar")))),
      h("tbody", {}, comp.map((c) => h("tr", {}, h("td", {}, c.column), h("td", {}, c.mapped), h("td", { class: "num" }, c.filled), h("td", {}, c.bar)))))),
    h("h4", {}, "Checks"),
    h("ul", { class: "issues small" }, q.issues.map((i) => h("li", {}, `${i.description}: ${i.count.toLocaleString()}`))),
    sourcesBlock([{ label: "Data quality profile", ...q.evidence }]));
}

/* ---------- Page 2: recommendations ---------- */
async function generate() {
  const btn = $("generate"); btn.disabled = true;
  loading($("candidates"), "analysis");
  try {
    const d = await api("/api/recommendations/generate", { method: "POST", body: bodyCtx() });
    S.candidates = d.candidates;
    $("ranking").textContent = `Snapshot ${d.snapshot.snapshot_id} · data as of ${d.snapshot.as_of.replace("T", " ").slice(0, 16)} UTC · period ${d.snapshot.period} · ranked by ${d.ranking}`;
    renderCandidates(d);
  } catch (e) { errorState($("candidates"), e); }
  finally { btn.disabled = false; }
}

function evidenceList(evidence) {
  return table([{ key: "label", label: "Measured evidence" }, { key: "display", label: "Value" }, { key: "n", label: "Records", unit: "count" }, { key: "query_id", label: "Query id" }],
    evidence.map((e) => ({ label: e.label, display: e.display, n: e.n, query_id: e.query_id })));
}

function candidateDetail(c) {
  return h("div", {},
    evidenceList(c.evidence),
    h("dl", { class: "kv" },
      h("dt", {}, "Problem"), h("dd", {}, c.problem),
      h("dt", {}, "Expected impact"), h("dd", { class: "projection" }, h("strong", {}, "Projection: "), c.expected_impact.statement,
        c.expected_impact.assumptions.length ? h("div", { class: "small muted" }, "Assumptions: " + c.expected_impact.assumptions.join(" ")) : null),
      h("dt", {}, "Priority"), h("dd", {}, `${c.priority.label} — score ${c.priority.score} (impact ${c.priority.impact}, urgency ${c.priority.urgency}, confidence ${c.priority.confidence})`),
      h("dt", {}, "Confidence"), h("dd", {}, c.confidence_note),
      h("dt", {}, "Effort"), h("dd", {}, c.effort.note),
      h("dt", {}, "Suggested owner"), h("dd", {}, c.owner_suggestion ? `${c.owner_suggestion.team} — ${c.owner_suggestion.basis}` : "None: the source does not support an assignment."),
      h("dt", {}, "Proposed steps"), h("dd", {}, h("ol", { class: "issues" }, c.actions.map((a) => h("li", {}, a)))),
      h("dt", {}, "Risks"), h("dd", {}, c.risks.join(" ")),
      c.dependencies && c.dependencies.length ? [h("dt", {}, "Dependencies"), h("dd", {}, c.dependencies.join(" "))] : null,
      h("dt", {}, "Cause"), h("dd", {}, c.causation_note),
      h("dt", {}, "Snapshot"), h("dd", { class: "mono" }, `${c.snapshot.snapshot_id} · registry ${c.snapshot.registry_version}`)),
    sourcesBlock(c.evidence.filter((e) => e.sql).map((e) => ({ label: e.label, query_id: e.query_id, sql: e.sql, params: e.params }))));
}

function renderCandidates(d) {
  const el = $("candidates");
  if (!d.candidates.length) { el.replaceChildren(h("div", { class: "state" }, "No segment meets the evidence thresholds for this period and filters.")); return; }
  const canAdd = S.me.permissions.includes("plan.add") && d.plan_enabled;
  el.replaceChildren(...d.candidates.map((c) => {
    const detail = h("div", { hidden: true }, candidateDetail(c));
    const status = h("span", { class: "small muted" }, c.in_plan ? `In plan as ${c.in_plan.rec_id} (${c.in_plan.status})` : "");
    const save = canAdd && !c.in_plan ? h("button", { type: "button", class: "primary", onclick: async (e) => {
      e.target.disabled = true;
      try { const r = await api("/api/plan", { method: "POST", body: { snapshot_id: c.snapshot.snapshot_id, key: c.key } });
        status.textContent = `Saved as ${r.rec_id} (proposed)`; e.target.remove(); loadPlan(); }
      catch (err) { status.textContent = err.message; e.target.disabled = false; } } }, "Save to plan") : null;
    return h("article", { class: "cand" },
      h("div", { class: "cand-head" },
        h("h3", {}, h("span", { class: "rank" }, "#" + c.rank), c.title),
        h("span", { class: "prio " + c.priority.label }, `${c.priority.label} · ${c.priority.score}`)),
      h("p", {}, c.summary),
      h("div", { class: "btns" },
        h("button", { type: "button", "aria-expanded": "false", onclick: (e) => { detail.hidden = !detail.hidden; e.target.setAttribute("aria-expanded", String(!detail.hidden)); } }, "Evidence and plan details"),
        c.drilldown ? h("button", { type: "button", onclick: () => openDrill(c.drilldown.metric_id, c.drilldown.filters, c.title) }, "View affected records") : null,
        save, status),
      detail);
  }));
}

async function loadPlan() {
  const el = $("plan");
  loading(el, "plan");
  try {
    const status = $("plan-status").value;
    const d = await api("/api/plan" + (status ? "?status=" + encodeURIComponent(status) : ""));
    if (!d.items.length) { el.replaceChildren(h("div", { class: "state" }, "Nothing saved yet. Generate recommendations and save the ones worth reviewing.")); return; }
    el.replaceChildren(h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Id", "Title", "Status", "Priority", "Owner", "Updated"].map((t) => h("th", { scope: "col" }, t)))),
      h("tbody", {}, d.items.map((r) => h("tr", {},
        h("td", {}, h("button", { type: "button", class: "link", onclick: () => showPlanItem(r.rec_id) }, r.rec_id)),
        h("td", {}, r.title), h("td", { class: "status" }, r.status.replace("_", " ")),
        h("td", {}, h("span", { class: "prio " + r.priority_label }, `${r.priority_label} · ${r.priority_score}`)),
        h("td", {}, r.owner || "—"), h("td", {}, (r.updated_at || "").replace("T", " ").slice(0, 16) + " by " + r.updated_by)))))));
  } catch (e) {
    if (e.status === 503) el.replaceChildren(h("div", { class: "state" }, e.message));
    else errorState(el, e);
  }
}

const NEXT = { proposed: ["reviewed", "rejected", "blocked"], reviewed: ["approved", "rejected", "blocked", "proposed"],
  approved: ["in_progress", "blocked", "rejected"], in_progress: ["completed", "blocked"], blocked: ["reviewed", "in_progress", "rejected"],
  completed: [], rejected: [] };
const NEEDS = { reviewed: "plan.review", proposed: "plan.review" };

async function showPlanItem(id) {
  const el = $("plan-detail");
  loading(el, id);
  try {
    const r = await api("/api/plan/" + encodeURIComponent(id));
    const p = r.payload;
    const note = h("input", { type: "text", id: "plan-note", maxlength: 2000, placeholder: "Note (required to reject or block)" });
    const msg = h("p", { class: "small", role: "status" });
    const act = async (path, body) => {
      try { await api(`/api/plan/${encodeURIComponent(id)}/${path}`, { method: "POST", body }); msg.textContent = "Saved."; await showPlanItem(id); loadPlan(); }
      catch (e) { msg.textContent = e.message; }
    };
    const perms = S.me.permissions;
    const buttons = (NEXT[r.status] || []).filter((s) => perms.includes(NEEDS[s] || "plan.approve"))
      .map((s) => h("button", { type: "button", onclick: () => act("status", { to: s, version: r.version, note: note.value }) }, "Mark " + s.replace("_", " ")));
    const owner = h("input", { type: "text", id: "plan-owner", maxlength: 200, placeholder: "Owner or team", value: r.owner || "" });
    el.replaceChildren(h("article", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", {}, `${r.rec_id} · ${r.title}`), h("span", { class: "status" }, r.status.replace("_", " "))),
      h("p", { class: "small muted" }, `Created ${r.created_at.replace("T", " ").slice(0, 16)} UTC by ${r.created_by} · data source ${r.data_source} · version ${r.version}`),
      h("p", {}, p.summary),
      candidateDetail(p),
      perms.includes("plan.review") ? h("div", { class: "plan-actions" },
        h("div", { class: "field" }, h("label", { for: "plan-note" }, "Note"), note), buttons,
        h("button", { type: "button", onclick: () => note.value.trim() && act("comment", { note: note.value }) }, "Add comment")) : null,
      perms.includes("plan.approve") && !["completed", "rejected"].includes(r.status) ? h("div", { class: "plan-actions" },
        h("div", { class: "field" }, h("label", { for: "plan-owner" }, "Assign owner"), owner),
        h("button", { type: "button", onclick: () => act("assign", { owner: owner.value, version: r.version, note: note.value }) }, "Assign")) : null,
      msg,
      h("h4", {}, "Audit trail"),
      h("ul", { class: "history" }, r.history.map((e) => h("li", {},
        `${e.event_at.replace("T", " ").slice(0, 16)} UTC — ${e.actor} ${e.action.replace("_", " ")}`,
        e.from_status !== e.to_status && e.to_status ? ` (${e.from_status || "new"} → ${e.to_status})` : "",
        e.detail && e.detail.to_owner ? ` to ${e.detail.to_owner}` : "", e.note ? ` — “${e.note}”` : "")))));
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { errorState(el, e); }
}

/* ---------- Page 3: assistant ---------- */
const EXAMPLES = ["What changed in service performance this month?", "Which categories contribute most to the current backlog?",
  "Compare SLA performance with the previous period.", "What are the strongest data-supported opportunities for improvement?",
  "Which regions have the longest resolution times?", "What data is missing before we can answer questions reliably?"];
function renderExamples() {
  $("examples").replaceChildren(...EXAMPLES.map((q) => h("button", { type: "button", class: "chip", onclick: () => ask(q) }, q)));
}

function renderAnswer(a) {
  const node = h("div", { class: "msg status-" + a.status });
  node.append(...a.text.map((t) => h("p", {}, t)));
  const meta = [];
  if (a.period) meta.push(`Period ${a.period}`);
  if (a.as_of) meta.push(`data as of ${a.as_of.replace("T", " ").slice(0, 16)} UTC`);
  if (a.filters && Object.keys(a.filters).length) meta.push("filters " + Object.entries(a.filters).map(([k, v]) => `${k}=${v.join("/")}`).join(", "));
  if (a.scope && Object.keys(a.scope).length) meta.push("your scope " + Object.entries(a.scope).map(([k, v]) => `${k}=${v.join("/")}`).join(", "));
  if (meta.length) node.append(h("p", { class: "notes" }, meta.join(" · ")));
  if (a.chart && a.chart.points.length) {
    const c = h("div", { class: "chart" });
    if (a.chart.type === "bar") barChart(c, a.chart.label, a.chart.points.map((p) => ({ segment: String(p.x), value: p.y, n: p.n })), a.chart.unit, null,
      a.can_drilldown && a.plan.metric_id ? (r) => openDrill(a.plan.metric_id, { [a.plan.dimension]: [r.segment] }, `${a.chart.label} — ${r.segment}`) : null);
    else lineChart(c, a.chart.label, a.chart.points, a.chart.unit);
    node.append(c);
  }
  if (a.table && a.table.rows.length) {
    const cols = a.table.columns.map((c) => ({ ...c }));
    node.append(h("details", { open: !a.chart }, h("summary", {}, "Table"), table(cols, a.table.rows)));
  }
  if (a.definitions && a.definitions.length) {
    node.append(h("details", {}, h("summary", {}, "Metric definitions"), a.definitions.map((d) => h("p", { class: "small" },
      h("strong", {}, d.name + ": "), d.description, " ", h("span", { class: "mono" }, d.expression + (d.filter ? " WHERE " + d.filter : ""))))));
  }
  if (a.limitations && a.limitations.length) node.append(h("details", {}, h("summary", {}, "Limitations"), h("ul", { class: "issues small" }, a.limitations.map((l) => h("li", {}, l)))));
  const src = sourcesBlock(a.sources); if (src) node.append(src);
  if (a.notes && a.notes.length) node.append(...a.notes.map((n) => h("p", { class: "notes" }, n)));
  if (a.drilldown && a.can_drilldown && a.status === "answered" && !a.chart) node.append(h("button", { type: "button", class: "link",
    onclick: () => openDrill(a.drilldown.metric_id, {}, "Records behind the answer") }, "Show the records behind this answer"));
  if (a.followups && a.followups.length) node.append(h("div", { class: "followups" }, a.followups.map((f) => h("button", { type: "button", class: "chip", onclick: () => ask(f) }, f))));
  return node;
}

let asking = false;
async function ask(text) {
  const msg = (text || $("message").value).trim();
  if (!msg || asking) return;
  asking = true; $("send").disabled = true;
  const log = $("chat-log");
  log.append(h("div", { class: "msg user" }, msg));
  const wait = h("div", { class: "msg" }, h("p", { class: "muted" }, "Working out which approved metric answers this and querying it…"));
  log.append(wait);
  $("message").value = "";
  try {
    const a = await api("/api/chat", { method: "POST", body: { message: msg, conversation_id: S.conversation, ...bodyCtx() } });
    S.conversation = a.conversation_id;
    wait.replaceWith(renderAnswer(a));
  } catch (e) {
    wait.replaceWith(h("div", { class: "msg status-error", role: "alert" }, h("p", {}, "No answer: " + e.message), h("p", { class: "notes" }, "Nothing was guessed.")));
  } finally {
    asking = false; $("send").disabled = false;
    log.lastElementChild.scrollIntoView({ behavior: "smooth", block: "end" });
  }
}

/* ---------- boot ---------- */
async function boot() {
  try {
    S.me = await api("/api/me");
  } catch (e) {
    if (e.status === 401) {
      document.querySelectorAll(".page").forEach((p) => (p.hidden = true));
      $("page-signin").hidden = false; $("freshness").textContent = "Sign in to continue"; return;
    }
    $("freshness").textContent = "Cannot reach the server: " + e.message; return;
  }
  try {
    S.meta = await api("/api/meta");
  } catch (e) {
    $("freshness").textContent = "Data unavailable: " + e.message;
    errorState($("kpis"), e); $("page-analysis").hidden = false; return;
  }
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem("si-view") || "null"); } catch { saved = null; }
  S.start = (saved && saved.start) || S.meta.default_period.start;
  S.end = (saved && saved.end) || S.meta.default_period.end;
  S.filters = (saved && saved.filters) || {};
  renderShell();
  route();
}

document.addEventListener("DOMContentLoaded", () => {
  window.addEventListener("hashchange", route);
  $("apply").addEventListener("click", () => { readFilters(); analysisKey = null; route(); });
  $("reset").addEventListener("click", () => { S.filters = {}; S.start = S.meta.default_period.start; S.end = S.meta.default_period.end; renderShell(); analysisKey = null; route(); });
  document.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => { preset(b.dataset.preset); readFilters(); analysisKey = null; route(); }));
  $("grain").addEventListener("change", loadTrend);
  $("dimension").addEventListener("change", loadBreakdown);
  $("drill-close").addEventListener("click", () => { $("drill-panel").hidden = true; });
  $("generate").addEventListener("click", generate);
  $("plan-status").addEventListener("change", loadPlan);
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); ask(); });
  $("message").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } });
  $("new-chat").addEventListener("click", () => { S.conversation = null; $("chat-log").replaceChildren(); });
  $("signin").addEventListener("submit", (e) => { e.preventDefault(); try { sessionStorage.setItem("si-token", $("token").value); } catch { /* tab only */ } boot(); });
  boot();
});
