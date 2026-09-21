/* ══════════════════════════════════════════════════════════════════════════
   AUTOMATIC SELECTOR DISCOVERY — evidence, not guesswork.
   ──────────────────────────────────────────────────────────────────────────
   This does NOT pick selectors by intuition. It proposes candidates from what
   the live DOM actually contains (labels, roles, accessible names, input
   types), and the Python side then PROVES each one by using it: type a serial
   that exists, type one that does not, and keep only what demonstrably behaves
   like a search box, a submit control, a results container and an empty state.
   Anything that cannot be proven is reported unresolved, never approximated.
   ══════════════════════════════════════════════════════════════════════════ */
(() => {
  if (window.__maiaDiscover) return;

  const PROBE = 'data-maia-probe';
  // What counts as the application chrome, across frameworks that use semantic
  // landmarks and those that only use class names.
  const LANDMARKS = ['header', '[role=banner]', 'nav', '[role=navigation]',
                     '[class*=header i]', '[class*=topbar i]', '[class*=navbar i]',
                     '[id*=header i]', '[id*=topbar i]', '[class*=masthead i]'];
  let counter = 0;

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (!(r.width || r.height)) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
  };
  const text = (el) => ((el.innerText || el.textContent || '') + '').trim().replace(/\s+/g, ' ');

  function accName(el) {
    const direct = el.getAttribute('aria-label') || el.getAttribute('placeholder')
                || el.getAttribute('title') || el.getAttribute('name');
    if (direct) return direct.trim();
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) return text(lab);
    }
    const wrap = el.closest('label');
    if (wrap) return text(wrap);
    return text(el).slice(0, 60);
  }

  function tag(el) {
    let key = el.getAttribute(PROBE);
    if (!key) { key = 'p' + (++counter); el.setAttribute(PROBE, key); }
    return key;
  }

  const describe = (el) => ({
    key: tag(el), tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type'), name: accName(el), text: text(el).slice(0, 80),
    visible: visible(el),
  });

  // ── candidate pools, ranked by how well the page's own words match ────────
  function inputCandidates() {
    const sel = "input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea, [role=searchbox], [role=textbox]";
    const wanted = /serial|s\/?n\b|pin\b|equipment|machine|asset|search|model/i;
    return [...document.querySelectorAll(sel)].filter(visible).map(el => {
      const d = describe(el);
      d.score = wanted.test(d.name + ' ' + (el.getAttribute('id') || '')) ? 2 : 1;
      return d;
    }).sort((a, b) => b.score - a.score).slice(0, 12);
  }

  function buttonCandidates() {
    const sel = "button, input[type=submit], input[type=button], [role=button], a.btn";
    const wanted = /search|find|go\b|submit|lookup|query|بحث/i;
    return [...document.querySelectorAll(sel)].filter(visible).map(el => {
      const d = describe(el);
      d.score = wanted.test(d.name + ' ' + d.text) ? 2 : 1;
      return d;
    }).sort((a, b) => b.score - a.score).slice(0, 12);
  }

  function navCandidates() {
    const wanted = /equipment|serial|machine|asset|search|parts/i;
    return [...document.querySelectorAll('a[href], [role=link], [role=menuitem], button')]
      .filter(visible).filter(el => wanted.test(text(el) + ' ' + accName(el)))
      .slice(0, 15).map(describe);
  }

  function consentCandidates() {
    const wanted = /accept|agree|allow|got it|ok\b|أوافق|قبول/i;
    return [...document.querySelectorAll('button, [role=button], a')]
      .filter(visible).filter(el => wanted.test(text(el)))
      .filter(el => {
        const banner = el.closest('[class*=cookie i], [class*=consent i], [id*=cookie i], [id*=consent i], [class*=onetrust i], [id*=onetrust i]');
        return !!banner;
      }).slice(0, 5).map(describe);
  }

  // ── outcome analysis: what appeared because of the search ─────────────────
  // The smallest visible element that carries the serial, plus the container
  // that holds it, is the result. Nothing is assumed about markup.
  function findByText(needle) {
    const hay = String(needle || '').toUpperCase();
    if (!hay) return [];
    const out = [];
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el)) continue;
      const t = text(el).toUpperCase();
      if (!t.includes(hay)) continue;
      // keep only the innermost elements carrying the text
      if ([...el.children].some(c => visible(c) && text(c).toUpperCase().includes(hay))) continue;
      out.push(el);
      if (out.length > 20) break;
    }
    return out.map(el => {
      const row = el.closest('tr, li, [role=row], [class*=row i], [class*=result i]') || el;
      // Start the container search ABOVE the row: a <tr class="result-row"> matches
      // [class*=result] itself, and a row is not its own container.
      const above = row.parentElement;
      const container = (above && above.closest('table, tbody, ul, ol, [role=table], [role=grid], [role=list], [class*=result i], [class*=list i]')) || above;
      return { cell: describe(el), row: describe(row),
               container: container ? describe(container) : null };
    });
  }

  const EMPTY_RE = /(no\s+(results?|records?|matches?|equipment|data)|not\s+found|nothing\s+found|0\s+results?|no\s+items|لا\s+توجد|لم\s+يتم\s+العثور)/i;
  function findEmptyState() {
    const hits = [];
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el)) continue;
      const t = text(el);
      if (t.length > 200 || !EMPTY_RE.test(t)) continue;
      if ([...el.children].some(c => visible(c) && EMPTY_RE.test(text(c)))) continue;
      hits.push(describe(el));
      if (hits.length > 6) break;
    }
    return hits;
  }

  // ── detail fields: read the page's own labels, take the adjacent value ────
  const FIELD_WORDS = {
    equipment_model: /\b(model|model\s*number|equipment\s*model)\b/i,
    equipment_type : /\b(type|equipment\s*type|family|product\s*family)\b/i,
    build_date     : /\b(build\s*date|manufactur(e|ed|ing)\s*date|date\s*built|year\s*built)\b/i,
    engine_family  : /\b(engine|engine\s*model|engine\s*family|engine\s*arrangement)\b/i,
    serial_number  : /\b(serial|serial\s*number|s\/?n|pin)\b/i,
  };

  // The four fields the detail page must yield, in the order they are read.
  // Each entry is the page's OWN wording, as a pattern — not a selector, and
  // not a value. The disambiguation is explicit: "Engine Serial Number" must
  // never be harvested as the machine serial, nor as the engine family.
  const LABELLED_FIELDS = [
    { field: 'machine_serial_number', label: 'Machine\\s+Serial\\s+(?:Number|No\\.?)' },
    { field: 'machine_build_date',    label: 'Machine\\s+Build\\s+Date' },
    { field: 'engine_serial_number',  label: 'Engine\\s+Serial\\s+(?:Number|No\\.?)' },
    { field: 'engine_build_date',     label: 'Engine\\s+Build\\s+Date' },
  ];
  // A generic label must not be claimed when it is really one of the four above.
  const RESERVED_LABEL = /\b(machine|engine)\s+(serial\s+(number|no\.?)|build\s+date)\b/i;

  function valueNextTo(labelEl) {
    // the value is the next sibling with text, or the second cell of the row
    const row = labelEl.closest('tr, [role=row]');
    if (row) {
      const cells = [...row.children].filter(visible);
      const idx = cells.indexOf(labelEl.closest('td, th, [role=cell]') || labelEl);
      if (idx >= 0 && cells[idx + 1]) return cells[idx + 1];
    }
    let sib = labelEl.nextElementSibling;
    while (sib && (!visible(sib) || !text(sib))) sib = sib.nextElementSibling;
    if (sib) return sib;
    const parent = labelEl.parentElement;
    if (parent) {
      const kids = [...parent.children].filter(c => visible(c) && text(c));
      const i = kids.indexOf(labelEl);
      if (i >= 0 && kids[i + 1]) return kids[i + 1];
    }
    return null;
  }

  function detailFields() {
    const out = {};
    const labels = [...document.querySelectorAll('body *')].filter(el => {
      if (!visible(el)) return false;
      // A column header is a header, not a field label: "Serial Number" in a
      // parts table heads a column, and its "value" is the next column's title.
      if (el.tagName === 'TH' || el.getAttribute('role') === 'columnheader') return false;
      if (el.closest('thead')) return false;
      const t = text(el);
      return t && t.length <= 40 && el.children.length === 0;
    });
    for (const [field, re] of Object.entries(FIELD_WORDS)) {
      for (const lab of labels) {
        const labelText = text(lab);
        if (RESERVED_LABEL.test(labelText)) continue;   // belongs to LABELLED_FIELDS
        if (!re.test(labelText)) continue;
        const val = valueNextTo(lab);
        if (!val || !text(val)) continue;
        out[field] = { label: text(lab), value: describe(val), value_text: text(val).slice(0, 80) };
        break;
      }
    }
    // Specification rows: a repeating two-column structure on the detail page.
    // Identified by shape, not by a guessed class name.
    const rowGroups = new Map();
    for (const row of document.querySelectorAll('tr, [role=row], [class*=spec i], [class*=row i]')) {
      if (!visible(row)) continue;
      const cells = [...row.children].filter(visible);
      if (cells.length < 2 || cells.length > 4) continue;
      const label = text(cells[0]), value = text(cells[1]);
      if (!label || !value || label.length > 40) continue;
      const parent = row.parentElement;
      if (!parent) continue;
      if (!rowGroups.has(parent)) rowGroups.set(parent, []);
      rowGroups.get(parent).push(row);
    }
    let best = null;
    for (const [, rows] of rowGroups) if (!best || rows.length > best.length) best = rows;
    if (best && best.length >= 2) {
      const cells = [...best[0].children].filter(visible);
      out.spec_rows = { label: 'specification rows', value: describe(best[0]),
                        value_text: `${best.length} rows × ${cells.length} cells`,
                        cell_tag: cells[0].tagName.toLowerCase() };
    }

    const link = [...document.querySelectorAll('a[href]')].filter(visible)
      .find(a => /parts?\s*(manual|catalog|book)|media/i.test(text(a) + ' ' + a.href));
    if (link) out.parts_manual_url = { label: 'parts manual link', value: describe(link),
                                       value_text: link.getAttribute('href') };
    return out;
  }

  function clearProbes() {
    document.querySelectorAll(`[${PROBE}]`).forEach(el => el.removeAttribute(PROBE));
    counter = 0;
  }

  // Probe the first VISIBLE element matching any of these selectors. Hidden
  // leftovers (a sign-in heading still in the DOM) must never be picked.
  function probeFirstVisible(selectors) {
    for (const sel of selectors) {
      for (const el of document.querySelectorAll(sel)) {
        if (visible(el) && text(el)) return describe(el);
      }
    }
    return null;
  }

  function visibleTexts(selectors, limit) {
    const out = [];
    for (const sel of selectors) {
      for (const el of document.querySelectorAll(sel)) {
        if (!visible(el) || el.children.length) continue;
        const t = text(el);
        if (t && t.length <= 40) out.push(t);
        if (out.length >= (limit || 60)) return out;
      }
    }
    return out;
  }

  // The first visible element in a landmark whose text is new since sign-in.
  function probeNewInLandmarks(known) {
    const seen = new Set(known || []);
    for (const sel of LANDMARKS) {
      for (const el of document.querySelectorAll(sel + ' *')) {
        if (!visible(el) || el.children.length) continue;
        const t = text(el);
        if (t && t.length <= 40 && !seen.has(t)) return describe(el);
      }
    }
    return null;
  }

  // ── the equipment-details section, below the fold ─────────────────────────
  // SIS renders these fields inside its own scrolling pane, off-screen. None of
  // it can be discovered — let alone proven — until the pane has been scrolled
  // and the SPA has finished rendering what the scroll revealed.
  const D = () => window.__maiaSisDom;

  async function revealDetailSection(reSrc, opts) {
    const dom = D();
    if (!dom) return { found: false, error: 'sis_dom.js was not injected' };
    const revealed = await dom.revealSection(reSrc, opts);
    const settled = await dom.settle({ limitMs: (opts && opts.settleMs) || 4000 });
    return Object.assign({}, revealed, { settle: settled });
  }

  /** Probe the pane that actually scrolls, so it can be captured and re-used. */
  function scrollPaneFor(reSrc) {
    const dom = D();
    if (!dom) return null;
    const el = dom.findTextEl(reSrc, 400);
    const pane = el ? dom.scrollParent(el) : (dom.scrollableContainers()[0] || null);
    if (!pane || pane === document.documentElement || pane === document.body
        || pane === document.scrollingElement) {
      return null;              // the window scrolls; there is no pane to capture
    }
    return Object.assign(describe(pane), { path: dom.cssPath(pane) });
  }

  /**
   * The four machine/engine fields, found by the page's own labels.
   * Returns, per field, the element to capture, the shape the value comes in
   * (inline "Label - Value", or a value beside a label) and what it reads
   * right now — the value is evidence for the proof, never a stored default.
   */
  function labelledFields() {
    const dom = D();
    const out = {};
    if (!dom) return out;
    for (const spec of LABELLED_FIELDS) {
      const hit = dom.findLabelled(spec.label);
      if (!hit) continue;
      out[spec.field] = {
        label_pattern: spec.label,
        label_text: hit.label_text || null,
        shape: hit.shape,
        value: describe(hit.element),
        value_text: String(hit.value || '').slice(0, 80),
      };
    }
    return out;
  }

  /** Which of the four are still missing — reported, never filled in. */
  function labelledFieldsMissing(found) {
    return LABELLED_FIELDS.map(s => s.field).filter(f => !(found || {})[f]);
  }

  /**
   * Every "Product - …" section, with its parts table described by shape.
   * The heading, the header-cell row and the first data row are all probed so
   * the Python side can turn each into a verified selector.
   */
  function productGroups() {
    const dom = D();
    if (!dom) return [];
    const heads = dom.productHeadings();
    const out = [];
    heads.forEach((head, i) => {
      const title = dom.text(head);
      const described = dom.tableAfter(head, heads[i + 1]);
      const serialMatch = title.match(/\(([^)]{2,40})\)\s*$/);
      const entry = {
        title,
        group_serial: serialMatch ? serialMatch[1].trim() : null,
        is_entire_group: /entire\s+group/i.test(title),
        heading: describe(head),
        table: null,
      };
      if (described) {
        entry.table = {
          container: describe(described.table),
          first_row: describe(described.rows[0]),
          header_cell: described.header.length ? describe(described.header[0]) : null,
          columns: described.columns,
          row_count: described.row_count,
          column_count: described.column_count,
          cell_tag: described.cell_tag,
        };
        entry.sample_row = dom.rowsToObjects(
          { columns: described.columns, rows: described.rows.slice(0, 1) }, 1)[0] || null;
      }
      out.push(entry);
    });
    return out;
  }

  /** How many elements a selector matches right now — used to prove row selectors. */
  function countMatching(selector) {
    try { return document.querySelectorAll(selector).length; }
    catch (e) { return -1; }
  }

  /** Whether a selector's matches are exactly the rows of one described table. */
  function selectorCoversRows(selector, containerKey) {
    try {
      const found = [...document.querySelectorAll(selector)];
      const container = containerKey
        ? document.querySelector(`[${PROBE}="${containerKey}"]`) : null;
      const inside = container ? found.filter(el => container.contains(el)) : found;
      return { total: found.length, inside: inside.length };
    } catch (e) {
      return { total: -1, inside: -1 };
    }
  }

  window.__maiaDiscover = {
    probeFirstVisible, visibleTexts, probeNewInLandmarks, LANDMARKS,
    inputCandidates, buttonCandidates, navCandidates, consentCandidates,
    findByText, findEmptyState, detailFields, clearProbes,
    revealDetailSection, scrollPaneFor, labelledFields, labelledFieldsMissing,
    productGroups, countMatching, selectorCoversRows,
    LABELLED_FIELDS,
    pageSignature: () => ({ url: location.href, title: document.title,
                            visibleText: (document.body.innerText || '').slice(0, 4000) }),
  };
})();
