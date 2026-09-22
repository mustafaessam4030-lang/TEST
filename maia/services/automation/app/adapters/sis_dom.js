/* ══════════════════════════════════════════════════════════════════════════
   SHARED SIS DOM READER — the single implementation of "how a value is read".

   Used by BOTH sides so they can never drift apart:
     • scripts/capture/discover.js   — to DISCOVER and prove selectors
     • adapters/cat_sis.py           — to READ values at run time

   It contains no site knowledge: no class names, no ids, no assumptions about
   Caterpillar markup. It only implements mechanical rules —
       "scroll the element that actually scrolls, not the window"
       "a value is the text after the label, or the element beside it"
       "the table of a heading is the first table that follows it"
   — and the caller supplies the labels and the proven selectors.

   Nothing here invents a value: every function returns null when the DOM does
   not contain what was asked for.
   ══════════════════════════════════════════════════════════════════════════ */
(() => {
  if (window.__maiaSisDom) return;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const visible = (el) => {
    if (!el || el.nodeType !== 1) return false;
    const r = el.getBoundingClientRect();
    if (!(r.width || r.height)) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
  };

  const text = (el) => ((el && (el.innerText || el.textContent)) || '')
    .replace(/ /g, ' ').trim().replace(/\s+/g, ' ');

  // ── scrolling ───────────────────────────────────────────────────────────
  // SIS renders inside its own scrolling pane. Scrolling `window` moves
  // nothing, so the target section never appears and the run reports a
  // "changed website" that never changed. Find what really scrolls.
  const SCROLLY = /(auto|scroll|overlay)/;

  function isScrollable(el) {
    if (!el || el.nodeType !== 1) return false;
    const st = getComputedStyle(el);
    if (!SCROLLY.test(st.overflowY)) return false;
    return el.scrollHeight - el.clientHeight > 40;
  }

  function depth(el) {
    let d = 0;
    for (let p = el.parentElement; p; p = p.parentElement) d++;
    return d;
  }

  /** Every pane that can scroll, innermost first, with the document last. */
  function scrollableContainers() {
    const out = [];
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el) || !isScrollable(el)) continue;
      out.push(el);
      if (out.length > 40) break;
    }
    out.sort((a, b) => depth(b) - depth(a));
    const doc = document.scrollingElement || document.documentElement;
    if (doc && doc.scrollHeight - doc.clientHeight > 40) out.push(doc);
    return out;
  }

  /** The pane an element actually lives in — this is "the SIS content area". */
  function scrollParent(el) {
    for (let p = el && el.parentElement; p; p = p.parentElement) {
      if (isScrollable(p)) return p;
    }
    const doc = document.scrollingElement || document.documentElement;
    return doc && doc.scrollHeight - doc.clientHeight > 40 ? doc : null;
  }

  function cssPath(el) {
    // A stable-ish path used only for reporting which pane was scrolled.
    if (!el || el.nodeType !== 1) return null;
    if (el === document.scrollingElement || el === document.documentElement) return ':root';
    if (el.id) return `#${el.id}`;
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && parts.length < 4; e = e.parentElement) {
      let part = e.tagName.toLowerCase();
      if (e.id) { parts.unshift(`#${e.id}`); break; }
      const cls = (e.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean)[0];
      if (cls) part += `.${cls}`;
      parts.unshift(part);
    }
    return parts.join(' > ');
  }

  // ── finding text ────────────────────────────────────────────────────────
  /** The innermost visible element whose text matches — never a whole page wrapper. */
  function findTextEl(reSrc, maxLen) {
    let re;
    try { re = new RegExp(reSrc, 'i'); } catch (e) { return null; }
    const limit = maxLen || 400;
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el)) continue;
      const t = text(el);
      if (!t || t.length > limit || !re.test(t)) continue;
      let innerHit = false;
      for (const c of el.children) {
        if (visible(c) && re.test(text(c))) { innerHit = true; break; }
      }
      if (!innerHit) return el;
    }
    return null;
  }

  /**
   * Scroll — the real pane, then the window — until the wanted section exists
   * AND is visible. Returns what happened, so the caller can report evidence
   * instead of a shrug.
   */
  async function revealSection(reSrc, opts) {
    opts = opts || {};
    const maxSteps = opts.maxSteps || 30;
    const pause = opts.pause || 300;
    const maxLen = opts.maxLen || 400;
    // When the pane was captured, scroll exactly that one; otherwise every pane
    // that can scroll is moved, and the one that mattered is reported back.
    // `container` may be an element handle (Playwright resolved the captured
    // selector, which is not always CSS) or a plain CSS selector.
    const pinned = !opts.container ? null
      : (opts.container.nodeType === 1 ? opts.container
                                       : document.querySelector(opts.container));

    let el = findTextEl(reSrc, maxLen);
    if (el) {
      try { el.scrollIntoView({ block: 'center' }); } catch (e) { /* older engines */ }
      await sleep(pause);
      el = findTextEl(reSrc, maxLen) || el;
      return { found: true, steps: 0, scrolled: false,
               pane: cssPath(scrollParent(el)), text: text(el).slice(0, 120) };
    }

    let steps = 0;
    // Reaching the bottom is not the end: an SPA renders the section a moment
    // AFTER the scroll that requested it, so stopping at "nothing moved" finds
    // an empty page and calls it a changed website. Stop only when the page has
    // also stopped growing.
    const quietLimit = opts.quietRounds || 4;
    let quiet = 0;
    let lastLength = -1;
    for (let i = 1; i <= maxSteps; i++) {
      steps = i;
      let moved = false;
      for (const c of (pinned ? [pinned] : scrollableContainers())) {
        const before = c.scrollTop;
        const jump = Math.max(160, Math.floor(c.clientHeight * 0.8));
        c.scrollTop = Math.min(before + jump, c.scrollHeight);
        if (c.scrollTop !== before) moved = true;
      }
      await sleep(pause);
      el = findTextEl(reSrc, maxLen);
      if (el) {
        try { el.scrollIntoView({ block: 'center' }); } catch (e) { /* noop */ }
        await sleep(pause);
        el = findTextEl(reSrc, maxLen) || el;
        return { found: true, steps, scrolled: true,
                 pane: cssPath(scrollParent(el)), text: text(el).slice(0, 120) };
      }
      const length = ((document.body && document.body.innerText) || '').length;
      const grew = length !== lastLength;
      lastLength = length;
      quiet = (moved || grew) ? 0 : quiet + 1;
      if (quiet >= quietLimit) break;   // at the bottom, and nothing more arrived
    }
    return { found: false, steps, scrolled: true, pane: null, text: null };
  }

  /** Wait for the SPA to stop rendering. Two identical samples, then stop. */
  async function settle(opts) {
    opts = opts || {};
    const limit = opts.limitMs || 6000;
    const interval = opts.interval || 250;
    let prev = -1, stable = 0, waited = 0;
    while (waited < limit) {
      const len = ((document.body && document.body.innerText) || '').length;
      if (len === prev) {
        if (++stable >= 2) return { settled: true, waited_ms: waited, length: len };
      } else { stable = 0; prev = len; }
      await sleep(interval);
      waited += interval;
    }
    return { settled: false, waited_ms: waited, length: prev };
  }

  // ── label → value ───────────────────────────────────────────────────────
  // SIS writes these two ways and we support exactly those two, both read from
  // the page's own words:
  //    inline    "<label> - <value>"                (one element)
  //    adjacent  <td><label></td><td><value></td>   (two elements)
  const SEPARATORS = '[-–—:]';

  function inlineRe(labelSrc) {
    // group 1 = the label as the page wrote it, group 2 = the value
    return new RegExp('^\\s*(' + labelSrc + ')\\s*' + SEPARATORS + '\\s*(\\S.*)$', 'i');
  }
  function exactRe(labelSrc) {
    return new RegExp('^\\s*(?:' + labelSrc + ')\\s*' + SEPARATORS + '?\\s*$', 'i');
  }

  /** The value beside a label: next table cell, next sibling, next child. */
  function valueNextTo(labelEl) {
    const row = labelEl.closest('tr, [role=row]');
    if (row) {
      const cells = [...row.children].filter(visible);
      const own = labelEl.closest('td, th, [role=cell], [role=gridcell]') || labelEl;
      const idx = cells.indexOf(own);
      if (idx >= 0 && cells[idx + 1] && text(cells[idx + 1])) return cells[idx + 1];
    }
    let sib = labelEl.nextElementSibling;
    while (sib && (!visible(sib) || !text(sib))) sib = sib.nextElementSibling;
    if (sib) return sib;
    const parent = labelEl.parentElement;
    if (parent) {
      const kids = [...parent.children].filter((c) => visible(c) && text(c));
      const i = kids.indexOf(labelEl);
      if (i >= 0 && kids[i + 1]) return kids[i + 1];
    }
    return null;
  }

  /**
   * Locate one labelled field. Returns the element that should be captured,
   * the shape (so the reader knows whether to strip the label) and the value
   * as the page currently shows it.
   */
  function findLabelled(labelSrc) {
    const inline = inlineRe(labelSrc);
    const exact = exactRe(labelSrc);

    // 1. inline "Label - Value", innermost match wins
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el)) continue;
      const t = text(el);
      if (!t || t.length > 200) continue;
      const m = t.match(inline);
      if (!m) continue;
      let innerHit = false;
      for (const c of el.children) {
        if (visible(c) && inline.test(text(c))) { innerHit = true; break; }
      }
      if (innerHit) continue;
      return { shape: 'inline', element: el, label_text: m[1].trim(),
               raw: t, value: m[2].trim() };
    }

    // 2. a label element with the value beside it
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el) || el.children.length) continue;
      const t = text(el);
      if (!t || !exact.test(t)) continue;
      const val = valueNextTo(el);
      const v = val ? text(val) : '';
      if (!v) continue;
      return { shape: 'adjacent', element: val, label_element: el, label_text: t,
               raw: v, value: v };
    }
    return null;
  }

  /** Read a value from an ALREADY-PROVEN element. Strips the label if inline. */
  function readLabelledFrom(el, labelSrc) {
    if (!el || !visible(el)) return null;
    const raw = text(el);
    if (!raw) return null;
    if (labelSrc) {
      const m = raw.match(inlineRe(labelSrc));
      if (m) return { raw, value: m[2].trim(), label: m[1].trim(), shape: 'inline' };
      // the selector points at the label itself: take what is beside it
      if (exactRe(labelSrc).test(raw)) {
        const val = valueNextTo(el);
        const v = val ? text(val) : '';
        return v ? { raw: v, value: v, shape: 'adjacent' } : null;
      }
    }
    return { raw, value: raw, shape: 'value' };
  }

  /** Same, from a CSS selector. Playwright-only selectors must use the element form. */
  function readLabelled(selector, labelSrc) {
    let el = null;
    try { el = document.querySelector(selector); } catch (e) { el = null; }
    return readLabelledFrom(el, labelSrc);
  }

  // ── tables ──────────────────────────────────────────────────────────────
  function headerCellsOf(table) {
    let cells = [...table.querySelectorAll('thead th, thead td, [role=columnheader]')]
      .filter(visible);
    if (cells.length) return cells;
    const headRow = [...table.querySelectorAll('tr, [role=row]')].find((r) => {
      const kids = [...r.children].filter(visible);
      return kids.length >= 2 && kids.every(
        (c) => c.tagName === 'TH' || c.getAttribute('role') === 'columnheader');
    });
    return headRow ? [...headRow.children].filter(visible) : [];
  }

  function bodyRowsOf(table) {
    let rows = [...table.querySelectorAll('tbody tr')].filter(visible);
    if (!rows.length) {
      rows = [...table.querySelectorAll('tr, [role=row]')].filter(visible).filter((r) => {
        const kids = [...r.children].filter(visible);
        return kids.length >= 2 && !kids.every(
          (c) => c.tagName === 'TH' || c.getAttribute('role') === 'columnheader');
      });
    }
    return rows.filter((r) => [...r.children].filter(visible).length >= 2);
  }

  function describeTable(table) {
    const rows = bodyRowsOf(table);
    if (!rows.length) return null;
    const header = headerCellsOf(table);
    const firstCells = [...rows[0].children].filter(visible);
    const tags = [...new Set(firstCells.map((c) => c.tagName.toLowerCase()))];
    return {
      table, rows, header,
      columns: header.map((h) => text(h)).filter(Boolean),
      cell_tag: tags.length === 1 ? tags[0] : null,
      row_count: rows.length,
      column_count: firstCells.length,
    };
  }

  /** The table belonging to a heading: the first one that follows it in the page. */
  function tableAfter(headingEl, stopBeforeEl) {
    const tables = [...document.querySelectorAll('table, [role=table], [role=grid]')]
      .filter(visible);
    for (const t of tables) {
      const pos = headingEl.compareDocumentPosition(t);
      if (!(pos & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
      if (pos & Node.DOCUMENT_POSITION_CONTAINS) continue;      // the heading is inside it
      if (stopBeforeEl) {
        const after = t.compareDocumentPosition(stopBeforeEl);
        if (!(after & Node.DOCUMENT_POSITION_FOLLOWING)) continue;  // belongs to a later heading
      }
      const described = describeTable(t);
      if (described) return described;
    }
    return null;
  }

  function rowsToObjects(described, limit) {
    const cap = limit || 500;
    const cols = described.columns;
    const out = [];
    for (const row of described.rows.slice(0, cap)) {
      const cells = [...row.children].filter(visible).map((c) => {
        const link = c.querySelector && c.querySelector('a[href]');
        const value = text(c);
        return link ? { text: value, href: link.getAttribute('href') } : { text: value };
      });
      if (!cells.some((c) => c.text)) continue;
      const record = { cells: cells.map((c) => c.text) };
      const links = {};
      cells.forEach((c, i) => { if (c.href) links[cols[i] || `col_${i + 1}`] = c.href; });
      if (Object.keys(links).length) record.links = links;
      if (cols.length) {
        const named = {};
        cells.forEach((c, i) => { if (cols[i]) named[cols[i]] = c.text; });
        record.values = named;
      }
      out.push(record);
    }
    return out;
  }

  // ── "Product - …" groups ────────────────────────────────────────────────
  // The page's own wording decides what a group is; we never hard-code one.
  // SIS names a group after its component, not the word "Product":
  //   "<component> - Entire Group (<serial>)"   "Product - Entire Group (…)"
  // so match the shape, not one vocabulary.
  const PRODUCT_SRC = '(\\bEntire\\s+Group\\b)|(^\\s*Product\\s*[-–—]\\s*\\S)';

  function productHeadings() {
    const re = new RegExp(PRODUCT_SRC, 'i');
    const hits = [];
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el)) continue;
      const t = text(el);
      if (!t || t.length > 200 || !re.test(t)) continue;
      let innerHit = false;
      for (const c of el.children) {
        if (visible(c) && re.test(text(c))) { innerHit = true; break; }
      }
      if (innerHit) continue;
      hits.push(el);
      if (hits.length >= 40) break;
    }
    return hits;
  }

  /**
   * Every "Product - …" section on the page, in page order, each with its
   * parts table read in full. `selector`, when given, is the proven heading
   * selector; without it the page's wording is used (discovery only).
   */
  function readProductGroupsFrom(headings, limit) {
    const heads = (headings && headings.length
      ? headings.filter((el) => el && el.nodeType === 1 && visible(el))
      : productHeadings());
    const groups = [];
    heads.forEach((head, i) => {
      const title = text(head);
      const described = tableAfter(head, heads[i + 1]);
      const serialMatch = title.match(/\(([^)]{2,40})\)\s*$/);
      groups.push({
        title,
        group_serial: serialMatch ? serialMatch[1].trim() : null,
        is_entire_group: /entire\s+group/i.test(title),
        columns: described ? described.columns : [],
        row_count: described ? described.row_count : 0,
        column_count: described ? described.column_count : 0,
        rows: described ? rowsToObjects(described, limit) : [],
      });
    });
    return groups;
  }

  /** Same, from a CSS selector (or null for "every Product - … heading"). */
  function readProductGroups(selector, limit) {
    let heads = null;
    if (selector) {
      try { heads = [...document.querySelectorAll(selector)]; } catch (e) { heads = null; }
    }
    return readProductGroupsFrom(heads, limit);
  }

  window.__maiaSisDom = {
    sleep, visible, text, isScrollable, scrollableContainers, scrollParent, cssPath,
    findTextEl, revealSection, settle,
    inlineRe, exactRe, valueNextTo, findLabelled, readLabelled, readLabelledFrom,
    headerCellsOf, bodyRowsOf, describeTable, tableAfter, rowsToObjects,
    productHeadings, readProductGroups, readProductGroupsFrom, PRODUCT_SRC,
  };
})();
