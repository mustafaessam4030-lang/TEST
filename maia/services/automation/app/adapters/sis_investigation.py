"""SIS Troubleshooting + 3D Model investigation, on the page a lookup already opened.

Runs inside the existing lookup (step INVESTIGATE), on the SAME browser context
and signed-in session, right after the record was extracted. It never logs in,
never searches, never opens another browser.

How it finds things — anchored to what the real SIS page shows, never guessed:

  * Tabs: the record's tab bar reads "Dashboard | Parts | Repair | Service |
    Troubleshooting | 3D Model". A tab is the element whose visible text is
    exactly that label AND that sits in the container holding "Dashboard" and
    "Parts". Same principle the Parts tab was learned by.
  * Troubleshooting: the left panel "Codes, Events, & Symptoms" lists sections
    whose headings read "Advanced Troubleshooting (283)", "Troubleshooting (30)",
    "Symptoms (36)". Each is expanded and its rows read as text, verbatim.
  * 3D Model: nothing is assumed about the viewer. It is INSTRUMENTED — canvases
    and WebGL, known viewer libraries, model files on the network, component
    trees in the DOM, what a click on the model reveals — and component names
    are used only when the viewer itself provides them.

Nothing here raises. Every part reports a status and a reason, and a failure
here never fails the lookup that carried it.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from app.core.logging import log

logger = logging.getLogger(__name__)

SECTION_RE = re.compile(r"^(Advanced Troubleshooting|Troubleshooting|Symptoms)\s*\((\d+)\)$")
MODEL_EXT = re.compile(r"\.(glb|gltf|bin|obj|fbx|stl|scs|svf2?|hsf|x3d|3ds|dae|usdz|ifc|zip)(\?|$)",
                       re.I)

PROBE_JS = r"""
(() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = el => { if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  window.__maiaInv = {
    norm, vis,
    // Mark the tab whose text is exactly `label` inside the record's tab bar.
    markTab(label) {
      document.querySelectorAll('[data-maia-tab]').forEach(e => e.removeAttribute('data-maia-tab'));
      const cands = [...document.querySelectorAll('a,button,[role=tab],li,span,div')]
        .filter(el => vis(el) && norm(el.innerText) === label);
      for (const el of cands) {
        let p = el;
        for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
          const t = norm(p.innerText);
          if (t.includes('Dashboard') && t.includes('Parts') && t.length < 250) {
            const target = el.closest('a,button,[role=tab]') || el;
            target.setAttribute('data-maia-tab', label);
            return {found: true, tag: target.tagName, candidates: cands.length};
          }
        }
      }
      return {found: false, candidates: cands.length};
    },
    // Troubleshooting sections: headings like "Troubleshooting (30)".
    markSections() {
      const re = /^(Advanced Troubleshooting|Troubleshooting|Symptoms)\s*\((\d+)\)$/;
      const out = [];
      const els = [...document.querySelectorAll('body *')].filter(el => vis(el) && re.test(norm(el.innerText)));
      const leaf = els.filter(el => ![...el.children].some(c => re.test(norm(c.innerText))));
      leaf.forEach((el, i) => {
        const m = norm(el.innerText).match(re);
        el.setAttribute('data-maia-tr-section', String(i));
        out.push({index: i, section: m[1], count: Number(m[2])});
      });
      const title = [...document.querySelectorAll('body *')].find(el =>
        vis(el) && /^Codes,\s*Events,\s*&\s*Symptoms$/i.test(norm(el.innerText)));
      return {sections: out, panel_title: title ? norm(title.innerText) : null};
    },
    // Rows between section `i`'s heading and the next heading, inside the panel.
    readSection(i, limit) {
      const head = document.querySelector(`[data-maia-tr-section="${i}"]`);
      if (!head) return {error: 'section heading not found'};
      const heads = [...document.querySelectorAll('[data-maia-tr-section]')];
      const next = heads.find(h => Number(h.getAttribute('data-maia-tr-section')) > i);
      const codeSection = !/^Symptoms/i.test(norm(head.innerText));
      let panel = head.parentElement;
      while (panel && !(heads.every(h => panel.contains(h)))) panel = panel.parentElement;
      panel = panel || document.body;
      const skip = /^(Keyword Filter|Select up to \d+ codes and symptoms)$/i;
      // a troubleshooting code at the start of a line: 36-1-5, 1-2, E360(2), …
      const CODE = /(^|\n)\s*((\d{1,4}-){1,2}\d{1,4}|E\d{2,5})(?=[\s:–—-]|$)/g;
      const codes = el => ((el.innerText || '').match(CODE) || []).length;
      const hasHead = el => heads.some(h => el.contains(h));
      const picked = [];
      for (const el of panel.querySelectorAll('*')) {
        if (!vis(el) || el === head || head.contains(el) || el.contains(head)) continue;
        if (!(head.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
        if (next && !(el.compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
        const hasTextChild = [...el.children].some(c => vis(c) && norm(c.innerText));
        if (hasTextChild) continue;                         // leaf elements only
        const t = norm(el.innerText);
        if (!t || t.length > 400 || skip.test(t)) continue;
        if (picked.some(r => r.contains(el))) continue;
        let row = el.closest('li,[role=option],[role=treeitem],[role=row],[role=listitem],label,tr');
        if (!row || !panel.contains(row) || hasHead(row)) {
          // no list/row element: the parent is the row, unless it holds several codes
          // (then it is the list itself, and this leaf is a row of its own — e.g. a label)
          const par = el.parentElement;
          row = par && !hasHead(par) && codes(par) <= 1 ? par : el;
        }
        // One entry per code: widen a row holding exactly one code to the
        // largest ancestor that still holds only that code (so the lines SIS
        // shows with it — e.g. the control module — stay with it).
        if (codeSection && codes(row) === 1) {
          let p = row.parentElement;
          while (p && p !== panel && !hasHead(p) && codes(p) === 1) { row = p; p = p.parentElement; }
        }
        if (hasHead(row) || picked.includes(row)) continue;
        picked.push(row);
        if (picked.length >= limit * 2) break;
      }
      const kept = picked.filter(r => !picked.some(o => o !== r && o.contains(r)));
      const items = [];
      for (const row of kept) {
        const rt = norm(row.innerText);
        if (!rt || heads.some(h => norm(h.innerText) === rt)) continue;
        const lines = (row.innerText || '').split('\n').map(norm).filter(Boolean)
          .filter((l, k, a) => k === 0 || l !== a[k - 1]);
        items.push({text: rt, lines});
        if (items.length >= limit) break;
      }
      // the scrollable element holding the rows, for virtualised lists
      let scroller = head.parentElement;
      while (scroller && scroller !== document.body && !(scroller.scrollHeight > scroller.clientHeight + 20)) scroller = scroller.parentElement;
      if (scroller && scroller !== document.body) scroller.setAttribute('data-maia-tr-scroll', String(i));
      const exp = head.closest('[aria-expanded]');
      return {rows: items.map(x => x.text), items, scrollable: !!(scroller && scroller !== document.body),
              expanded: exp ? exp.getAttribute('aria-expanded') : null};
    },
    // A structural sketch of the panel (tags, roles, classes, short text) — no
    // attribute values beyond those, no URLs, no inputs. For diagnosing layouts.
    panelSketch(maxChars) {
      const head = document.querySelector('[data-maia-tr-section]');
      if (!head) return null;
      const heads = [...document.querySelectorAll('[data-maia-tr-section]')];
      let panel = head.parentElement;
      while (panel && !(heads.every(h => panel.contains(h)))) panel = panel.parentElement;
      if (!panel) return null;
      let out = '';
      const walk = (el, depth) => {
        if (out.length > maxChars || !vis(el)) return;
        const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => norm(n.textContent)).join(' ').trim();
        const cls = (el.getAttribute('class') || '').split(/\s+/).slice(0, 3).join('.');
        const role = el.getAttribute('role');
        out += '  '.repeat(Math.min(depth, 20)) + '<' + el.tagName.toLowerCase() + (cls ? '.' + cls : '')
             + (role ? ' role=' + role : '') + '>' + (own ? ' ' + own.slice(0, 120) : '') + '\n';
        for (const c of el.children) walk(c, depth + 1);
      };
      walk(panel, 0);
      return out.slice(0, maxChars);
    },
    scrollSection(i) {
      const s = document.querySelector(`[data-maia-tr-scroll="${i}"]`);
      if (!s) return false;
      const before = s.scrollTop; s.scrollTop = before + s.clientHeight * 0.9;
      return s.scrollTop !== before;
    },
    // 3D viewer instrumentation — read-only.
    inspect3d() {
      const canvases = [...document.querySelectorAll('canvas')].filter(vis).map(c => {
        const r = c.getBoundingClientRect();
        return {width: Math.round(r.width), height: Math.round(r.height), id: c.id || null,
                cls: (c.className && String(c.className).slice(0, 80)) || null};
      });
      const libs = {};
      const known = ['THREE', 'BABYLON', 'Autodesk', 'Communicator', 'hwv', 'xeogl', 'x3dom',
                     'Cesium', 'pc', 'NOP_VIEWER', 'viewer', 'Viewer', 'Module'];
      known.forEach(k => { try { if (window[k] !== undefined) libs[k] = typeof window[k]; } catch (e) {} });
      const suspicious = Object.keys(window).filter(k => /viewer|model|scene|three|babylon|hoops|forge|webgl|gltf|glb|cad|part|assembl/i.test(k)).slice(0, 60);
      const iframes = [...document.querySelectorAll('iframe')].map(f => (f.getAttribute('src') || '').split('?')[0]).slice(0, 10);
      const trees = [...document.querySelectorAll('[role=tree],[role=treeitem],[class*=tree i],[class*=assembly i],[class*=component-list i],[class*=model-browser i]')]
        .filter(vis).slice(0, 20).map(el => ({tag: el.tagName, cls: String(el.className).slice(0, 80), text: norm(el.innerText).slice(0, 200)}));
      const buttons = [...document.querySelectorAll('button,[role=button]')].filter(vis)
        .map(b => norm(b.getAttribute('title') || b.getAttribute('aria-label') || b.innerText)).filter(Boolean).slice(0, 40);
      const resources = performance.getEntriesByType('resource').map(r => r.name.split('?')[0]).slice(-400);
      return {canvases, libs, suspicious_globals: suspicious, iframes, tree_elements: trees,
              toolbar: buttons, resources};
    },
    // Component names — only from APIs the viewer itself exposes.
    componentNames(max) {
      const out = {source: null, names: []};
      try {
        const v = window.NOP_VIEWER;
        if (v && v.model && v.model.getInstanceTree) {
          const t = v.model.getInstanceTree(); const names = [];
          const walk = id => { if (names.length >= max) return;
            names.push({id, name: t.getNodeName(id)});
            t.enumNodeChildren(id, c => walk(c)); };
          walk(t.getRootId());
          out.source = 'autodesk_viewer_api'; out.names = names; return out;
        }
      } catch (e) { out.error = 'autodesk: ' + e; }
      try {
        const h = window.hwv;
        if (h && h.model && h.model.getAbsoluteRootNode) {
          const names = [];
          const walk = id => { if (names.length >= max) return;
            names.push({id, name: h.model.getNodeName(id)});
            (h.model.getNodeChildren(id) || []).forEach(walk); };
          walk(h.model.getAbsoluteRootNode());
          out.source = 'hoops_communicator_api'; out.names = names; return out;
        }
      } catch (e) { out.error = 'hoops: ' + e; }
      try {
        const scene = window.BABYLON && window.BABYLON.Engine && window.BABYLON.Engine.LastCreatedScene;
        if (scene && scene.meshes) {
          out.source = 'babylon_scene'; out.names = scene.meshes.slice(0, max).map(m => ({id: m.uniqueId, name: m.name}));
          return out;
        }
      } catch (e) { out.error = 'babylon: ' + e; }
      const items = [...document.querySelectorAll('[role=treeitem]')].filter(vis).slice(0, max);
      if (items.length) {
        items.forEach((el, i) => el.setAttribute('data-maia-node', String(i)));
        out.source = 'dom_tree'; out.names = items.map((el, i) => ({id: i, name: norm(el.innerText)}));
      }
      return out;
    },
    highlight(source, id) {
      try {
        if (source === 'autodesk_viewer_api') { window.NOP_VIEWER.select([id]); window.NOP_VIEWER.fitToView([id]); return {ok: true, how: 'viewer.select + fitToView'}; }
        if (source === 'hoops_communicator_api') { window.hwv.selectionManager.selectNode(id); window.hwv.view.fitNodes([id]); return {ok: true, how: 'selectionManager.selectNode + view.fitNodes'}; }
        if (source === 'dom_tree') { const el = document.querySelector(`[data-maia-node="${id}"]`); if (el) { el.scrollIntoView({block: 'center'}); el.click(); return {ok: true, how: 'clicked the viewer tree item'}; } }
      } catch (e) { return {ok: false, error: String(e).slice(0, 200)}; }
      return {ok: false, error: 'highlighting is not supported for ' + source};
    },
    viewerText() {
      const c = document.querySelector('canvas'); if (!c) return '';
      let p = c.parentElement; for (let i = 0; i < 4 && p && p.parentElement; i++) p = p.parentElement;
      return norm((p || document.body).innerText).slice(0, 4000);
    },
  };
  return true;
})()
"""


class SisInvestigator:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = (config or {}).get("investigation") or {}
        self.row_limit = int(cfg.get("max_rows_per_section", 600))
        self.scroll_rounds = int(cfg.get("max_scroll_rounds", 25))
        self.canvas_wait_ms = int(cfg.get("canvas_wait_ms", 20000))
        self.click_probe = bool(cfg.get("click_probe", True))
        self.max_names = int(cfg.get("max_component_names", 5000))

    async def _js(self, ctx: Any, expr: str, arg: Any = None) -> Any:
        return await ctx.page.evaluate(expr, arg) if arg is not None else \
            await ctx.page.evaluate(expr)

    async def _settle(self, ctx: Any, ms: int = 3000) -> None:
        try:
            await ctx.page.evaluate("(ms) => window.__maiaSisDom && window.__maiaSisDom.settle"
                                    "({limitMs: ms})", ms)
        except Exception:
            await ctx.page.wait_for_timeout(min(ms, 1500))

    async def _shot(self, ctx: Any, name: str, shots: dict[str, str]) -> None:
        try:
            path = ctx.artifact_dir / f"{name}.png"
            await ctx.page.screenshot(path=str(path), full_page=False)
            shots[name] = str(path)
        except Exception as exc:
            log(logger, logging.WARNING, "sis.investigate_shot_failed", name=name,
                error=str(exc)[:120])

    async def open_tab(self, ctx: Any, label: str) -> dict[str, Any]:
        await self._js(ctx, PROBE_JS)
        marked = await self._js(ctx, "(l) => window.__maiaInv.markTab(l)", label)
        if not marked.get("found"):
            return {"opened": False, "reason": f"tab '{label}' not found in the record's tab bar",
                    "candidates": marked.get("candidates")}
        await ctx.page.locator(f'[data-maia-tab="{label}"]').first.click(timeout=8000)
        await ctx.page.wait_for_timeout(1200)
        await self._settle(ctx)
        return {"opened": True, "url": ctx.page.url}

    # ── troubleshooting ─────────────────────────────────────────────────────
    async def _read_section(self, ctx: Any, i: int, count: int) -> list[dict[str, Any]]:
        """Rows of section `i`, scrolling a virtualised list until nothing new appears."""
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _ in range(self.scroll_rounds):
            got = await self._js(ctx, "(a) => window.__maiaInv.readSection(a[0], a[1])",
                                 [i, self.row_limit])
            before = len(items)
            for x in got.get("items") or [{"text": r, "lines": [r]} for r in got.get("rows") or []]:
                if x["text"] not in seen:
                    seen.add(x["text"])
                    items.append(x)
            if len(items) >= min(count, self.row_limit) or not items or \
                    (len(items) == before and not got.get("scrollable")):
                break
            moved = await self._js(ctx, "(i) => window.__maiaInv.scrollSection(i)", i)
            if not moved and len(items) == before:
                break
            await ctx.page.wait_for_timeout(350)
        return items

    async def troubleshooting(self, ctx: Any, shots: dict[str, str]) -> dict[str, Any]:
        started = time.monotonic()
        tab = await self.open_tab(ctx, "Troubleshooting")
        out: dict[str, Any] = {"source": "SIS", "classification": "DIRECT",
                               "retrieved_at": datetime.now(timezone.utc).isoformat(),
                               "url": ctx.page.url, "tab": tab}
        if not tab["opened"]:
            return {**out, "status": "NOT_AVAILABLE", "reason": tab["reason"]}
        marked = await self._js(ctx, "() => window.__maiaInv.markSections()")
        out["panel_title"] = marked.get("panel_title")
        sections = []
        for sec in marked.get("sections") or []:
            i = sec["index"]
            try:
                # A section may already be open: read first, and click the
                # heading only when nothing shows (a click on an open section
                # would collapse it). Two clicks at most — open, or re-open.
                items, clicks = await self._read_section(ctx, i, sec["count"]), 0
                while not items and clicks < 2:
                    await ctx.page.locator(f'[data-maia-tr-section="{i}"]').first.click(timeout=6000)
                    clicks += 1
                    await ctx.page.wait_for_timeout(900)
                    await self._settle(ctx, 2500)
                    await self._js(ctx, PROBE_JS)
                    items = await self._read_section(ctx, i, sec["count"])
                sections.append({"section": sec["section"], "count_displayed": sec["count"],
                                 "items_read": len(items), "rows": [x["text"] for x in items],
                                 "items": items, "clicks": clicks})
            except Exception as exc:
                sections.append({"section": sec["section"], "count_displayed": sec["count"],
                                 "items_read": 0, "rows": [], "error": str(exc)[:160]})
        try:
            out["panel_sketch"] = await self._js(ctx, "(n) => window.__maiaInv.panelSketch(n)",
                                                 12000)
        except Exception:
            out["panel_sketch"] = None
        await self._shot(ctx, "troubleshooting", shots)
        out["sections"] = sections
        out["status"] = ("CAPTURED" if any(s["items_read"] for s in sections)
                         else "COUNTS_ONLY" if sections else "NOT_AVAILABLE")
        if not sections:
            out["reason"] = "no 'Codes, Events, & Symptoms' sections found on the tab"
        out["ms"] = int((time.monotonic() - started) * 1000)
        log(logger, logging.INFO, "sis.troubleshooting_read", run_id=getattr(ctx, "run_id", None),
            status=out["status"], sections=[(s["section"], s["count_displayed"], s["items_read"])
                                            for s in sections])
        return out

    # ── 3D model ────────────────────────────────────────────────────────────
    async def model_3d(self, ctx: Any, shots: dict[str, str]) -> dict[str, Any]:
        started = time.monotonic()
        responses: list[dict[str, Any]] = []

        def on_response(resp: Any) -> None:
            try:
                url = resp.url.split("?")[0]
                ctype = (resp.headers or {}).get("content-type", "")
                if MODEL_EXT.search(url) or "model" in ctype or "octet-stream" in ctype \
                        or re.search(r"3d|model|viewer|geometry|scene", url, re.I):
                    responses.append({"url": url[:300], "status": resp.status,
                                      "content_type": ctype[:80]})
            except Exception:
                pass

        ctx.page.on("response", on_response)
        try:
            tab = await self.open_tab(ctx, "3D Model")
            out: dict[str, Any] = {"source": "SIS 3D Model", "tab": tab, "url": ctx.page.url,
                                   "retrieved_at": datetime.now(timezone.utc).isoformat()}
            if not tab["opened"]:
                return {**out, "status": "NOT_AVAILABLE", "reason": tab["reason"]}
            try:
                await ctx.page.wait_for_selector("canvas", state="visible",
                                                 timeout=self.canvas_wait_ms)
            except Exception:
                pass
            await ctx.page.wait_for_timeout(2500)
            await self._js(ctx, PROBE_JS)
            viewer = await self._js(ctx, "() => window.__maiaInv.inspect3d()")
            viewer["model_resources"] = sorted({r for r in viewer.pop("resources", [])
                                                if MODEL_EXT.search(r)})[:50]
            names = await self._js(ctx, "(m) => window.__maiaInv.componentNames(m)",
                                   self.max_names)
            await self._shot(ctx, "model3d", shots)
            probe = None
            if self.click_probe and viewer.get("canvases"):
                probe = await self._click_probe(ctx)
            out.update({
                "viewer": viewer, "network": responses[:80],
                "component_names_source": names.get("source"),
                "component_names": [n for n in names.get("names") or [] if n.get("name")],
                "api_error": names.get("error"), "click_probe": probe,
            })
            has_canvas = bool(viewer.get("canvases"))
            if out["component_names"]:
                out["status"] = "METADATA_AVAILABLE"
            elif viewer.get("libs") or viewer.get("model_resources") or (probe or {}).get(
                    "new_text"):
                out["status"] = "API_DETECTED_NO_NAMES"
            elif has_canvas:
                out["status"] = "VISUAL_ONLY"
            else:
                out["status"] = "NO_VIEWER_FOUND"
            out["ms"] = int((time.monotonic() - started) * 1000)
            log(logger, logging.INFO, "sis.model3d_inspected", run_id=getattr(ctx, "run_id", None),
                status=out["status"], canvases=len(viewer.get("canvases") or []),
                libs=list((viewer.get("libs") or {}).keys()),
                names=len(out["component_names"]), names_source=out["component_names_source"])
            return out
        finally:
            try:
                ctx.page.remove_listener("response", on_response)
            except Exception:
                pass

    async def _click_probe(self, ctx: Any) -> dict[str, Any]:
        """Click the model's centre once and record what the page reveals. A
        selection panel with a part name is evidence of selection support."""
        try:
            before = await self._js(ctx, "() => window.__maiaInv.viewerText()")
            box = await ctx.page.locator("canvas").first.bounding_box()
            if not box:
                return {"clicked": False}
            await ctx.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            await ctx.page.wait_for_timeout(1200)
            after = await self._js(ctx, "() => window.__maiaInv.viewerText()")
            new = [t for t in after.split(" ") if t and t not in set(before.split(" "))]
            return {"clicked": True, "new_text": " ".join(new)[:400]}
        except Exception as exc:
            return {"clicked": False, "error": str(exc)[:160]}

    async def highlight(self, ctx: Any, source: str, node_id: Any,
                        shots: dict[str, str]) -> dict[str, Any]:
        try:
            result = await self._js(ctx, "(a) => window.__maiaInv.highlight(a[0], a[1])",
                                    [source, node_id])
            await ctx.page.wait_for_timeout(1200)
            await self._shot(ctx, "model3d_component", shots)
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}

    # ── the whole investigation ─────────────────────────────────────────────
    async def run(self, ctx: Any, serial: str, what: list[str],
                  locate: list[str] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        from app.analysis.component_mapper import map_targets
        from app.analysis.troubleshooting_analyzer import parse_troubleshooting

        shots: dict[str, str] = {}
        result: dict[str, Any] = {"serial": serial, "requested": sorted(set(what))}
        try:
            if "troubleshooting" in what:
                result["troubleshooting"] = await self.troubleshooting(ctx, shots)
            if "model_3d" in what:
                model = await self.model_3d(ctx, shots)
                result["model_3d"] = model
                targets = list(locate or [])
                if "troubleshooting" in result:
                    parsed = parse_troubleshooting(result["troubleshooting"])
                    targets += [e["component"] for e in parsed["entries"] if e.get("component")]
                if targets and model.get("component_names"):
                    mapping = map_targets(sorted(set(targets)), model["component_names"])
                    result["mapping"] = mapping
                    verified = [m for m in mapping if m["status"] == "VERIFIED"]
                    if verified:
                        first = verified[0]
                        result["highlight"] = {
                            "target": first["target"], "component": first["component"],
                            **await self.highlight(ctx, model["component_names_source"],
                                                   first["component"]["id"], shots)}
        except Exception as exc:                      # never fail the lookup
            result["error"] = str(exc)[:300]
            log(logger, logging.WARNING, "sis.investigate_failed", run_id=getattr(ctx, "run_id", None),
                error=str(exc)[:200])
        return result, shots
