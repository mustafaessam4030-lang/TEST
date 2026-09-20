/* ══════════════════════════════════════════════════════════════════════════
   MAYA SELECTOR PICKER — injected into the live page during capture.
   ──────────────────────────────────────────────────────────────────────────
   Click-to-pick overlay + candidate generator. The operator points at the real
   element; this file derives candidate selectors from what is actually in the
   DOM and verifies each one resolves back to that exact element, uniquely.
   Nothing is ever produced from a guess: if no candidate survives verification,
   the target is reported as unresolved and the caller marks it TODO_CAPTURE.
   ══════════════════════════════════════════════════════════════════════════ */
(() => {
  if (window.__mayaPicker) return;               // idempotent across SPA re-renders

  // Attributes we must never build a selector from.
  //  - data-maya-fixture: only exists in our own offline test fixture
  //  - ng-*/cdk-*/_ngcontent-*: framework bookkeeping, changes per build
  const BANNED_ATTRS = ['data-maya-fixture', 'data-maya-probe'];
  const UNSTABLE_CLASS = /^(ng-|cdk-|mat-focus|mdc-|is-|has-|active$|open$|selected$|focus$|hover$|touched$|dirty$|valid$|invalid$|pristine$)/;
  const UNSTABLE_ID = /^(mat-|cdk-|ng-|radix-|react-|:r[0-9a-z]+:|ember)/i;
  const AUTO_ID_TAIL = /[-_]?\d{3,}$/;           // foo-1042 style generated ids

  const css = (v) => (window.CSS && CSS.escape ? CSS.escape(v) : String(v).replace(/(["\\\]\[\.#:])/g, '\\$1'));
  // Inside a quoted attribute selector only " and \ need escaping — CSS.escape here
  // would turn `Engine family` into `Engine\ family`, which is valid but unreadable.
  const attrVal = (v) => String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const txt = (el) => ((el && (el.innerText || el.textContent || el.value || '')) || '').trim().replace(/\s+/g, ' ').slice(0, 120);

  // ── accessible-name + role heuristics (enough for a role= selector) ──────
  function roleOf(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit.trim().split(/\s+/)[0];
    const tag = el.tagName.toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'table') return 'table';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'submit' || t === 'button' || t === 'reset') return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'search') return 'searchbox';
      if (['text', 'email', 'tel', 'url', 'password', 'number'].includes(t)) return 'textbox';
    }
    return null;
  }

  function accessibleName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return aria.trim();
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const parts = labelledby.split(/\s+/).map(id => document.getElementById(id))
        .filter(Boolean).map(n => txt(n));
      if (parts.join(' ').trim()) return parts.join(' ').trim();
    }
    if (el.id) {
      const label = document.querySelector(`label[for="${css(el.id)}"]`);
      if (label && txt(label)) return txt(label);
    }
    const wrapping = el.closest('label');
    if (wrapping && txt(wrapping)) return txt(wrapping);
    const placeholder = el.getAttribute('placeholder');
    if (placeholder && placeholder.trim()) return placeholder.trim();
    const title = el.getAttribute('title');
    if (title && title.trim()) return title.trim();
    const own = txt(el);
    return own && own.length <= 60 ? own : '';
  }

  // ── stable CSS path: anchor on the nearest hooked ancestor, then descend ──
  function classPart(el) {
    const classes = (el.getAttribute('class') || '').trim().split(/\s+/)
      .filter(c => c && !UNSTABLE_CLASS.test(c) && !/^[a-z]+-\d+$/.test(c) && c.length < 40)
      .slice(0, 2);
    return classes.length ? '.' + classes.map(css).join('.') : '';
  }

  function hookFor(el) {
    for (const a of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v) return `[${a}="${attrVal(v)}"]`;
    }
    if (el.id && !UNSTABLE_ID.test(el.id) && !AUTO_ID_TAIL.test(el.id)) return `#${css(el.id)}`;
    return null;
  }

  function cssPath(el) {
    const parts = [];
    let node = el;
    for (let depth = 0; node && node.nodeType === 1 && depth < 6; depth++) {
      const anchor = depth > 0 ? hookFor(node) : null;
      if (anchor) { parts.unshift(anchor); return parts.join(' > '); }
      const tag = node.tagName.toLowerCase();
      let part = tag + classPart(node);
      const parent = node.parentElement;
      if (parent) {
        const sameShape = [...parent.children].filter(c => c.tagName === node.tagName
          && (c.getAttribute('class') || '') === (node.getAttribute('class') || ''));
        if (sameShape.length > 1) part += `:nth-of-type(${[...parent.children]
          .filter(c => c.tagName === node.tagName).indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
      if (node === document.body) break;
    }
    return parts.join(' > ');
  }

  function xPath(el) {
    const parts = [];
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const tag = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (!parent) { parts.unshift(`/${tag}`); break; }
      const idx = [...parent.children].filter(c => c.tagName === node.tagName).indexOf(node) + 1;
      parts.unshift(`/${tag}[${idx}]`);
      if (node === document.body) break;
    }
    return 'xpath=/html' + parts.join('');
  }

  // ── verification: a candidate counts only if it resolves to THIS element ──
  function resolvesUniquely(selector, el) {
    try {
      if (selector.startsWith('xpath=')) {
        const it = document.evaluate(selector.slice(6), document, null,
          XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        return { count: it.snapshotLength, isTarget: it.snapshotLength > 0 && it.snapshotItem(0) === el };
      }
      const found = document.querySelectorAll(selector);
      return { count: found.length, isTarget: found.length > 0 && found[0] === el };
    } catch (err) {
      return { count: 0, isTarget: false, error: String(err && err.message || err) };
    }
  }

  function build(el) {
    if (!el || el.nodeType !== 1) return { candidates: [], info: null };
    const tag = el.tagName.toLowerCase();
    const raw = [];

    for (const attr of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = el.getAttribute(attr);
      if (v && !BANNED_ATTRS.includes(attr)) raw.push({ selector: `[${attr}="${attrVal(v)}"]`, strategy: 'data-testid', rank: 1 });
    }
    if (el.id) {
      const unstable = UNSTABLE_ID.test(el.id) || AUTO_ID_TAIL.test(el.id);
      raw.push({ selector: `#${css(el.id)}`, strategy: 'id', rank: unstable ? 7 : 2,
                 note: unstable ? 'id looks framework-generated; may change between builds' : undefined });
    }
    const name = el.getAttribute('name') || el.getAttribute('formcontrolname');
    if (name) raw.push({ selector: `${tag}[name="${attrVal(name)}"]`, strategy: 'name', rank: 3 });
    const aria = el.getAttribute('aria-label');
    if (aria) raw.push({ selector: `${tag}[aria-label="${attrVal(aria)}"]`, strategy: 'aria-label', rank: 4 });

    const role = roleOf(el), accName = accessibleName(el);
    if (role && accName) raw.push({ selector: `role=${role}[name="${accName.replace(/"/g, '\\"')}"]`,
                                    strategy: 'role+name', rank: 5, needsPlaywright: true });

    const path = cssPath(el);
    if (path) raw.push({ selector: path, strategy: 'css-path', rank: 6 });
    raw.push({ selector: xPath(el), strategy: 'xpath', rank: 9,
               note: 'structural; breaks on any layout change — last resort' });

    const seen = new Set();
    const candidates = [];
    for (const c of raw.sort((a, b) => a.rank - b.rank)) {
      if (seen.has(c.selector)) continue;
      seen.add(c.selector);
      if (c.needsPlaywright) { candidates.push({ ...c, verified: null, count: null }); continue; }
      const check = resolvesUniquely(c.selector, el);
      candidates.push({ ...c, verified: !!(check.isTarget && check.count === 1),
                        count: check.count, error: check.error });
    }

    const rect = el.getBoundingClientRect();
    return {
      candidates,
      info: {
        tag, element_text: txt(el), url: location.href,
        visible: !!(rect.width || rect.height),
        attributes: Object.fromEntries([...el.attributes]
          .filter(a => !BANNED_ATTRS.includes(a.name))
          .map(a => [a.name, String(a.value).slice(0, 120)])),
        role, accessible_name: accName,
      },
    };
  }

  // ── overlay ──────────────────────────────────────────────────────────────
  let active = false, hud = null, box = null, hovered = null;

  function ensureChrome() {
    if (hud) return;
    hud = document.createElement('div');
    hud.id = '__maya_hud';
    hud.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:2147483647;background:#111;' +
      'color:#FFC500;font:600 13px/1.5 system-ui,sans-serif;padding:10px 16px;' +
      'box-shadow:0 2px 12px rgba(0,0,0,.4);display:none;pointer-events:none;';
    box = document.createElement('div');
    box.id = '__maya_box';
    box.style.cssText = 'position:fixed;z-index:2147483646;border:2px solid #FFC500;' +
      'background:rgba(255,197,0,.14);pointer-events:none;display:none;border-radius:2px;';
    document.documentElement.appendChild(hud);
    document.documentElement.appendChild(box);
  }

  function paint(el) {
    if (!el || !box) return;
    const r = el.getBoundingClientRect();
    Object.assign(box.style, { display: 'block', left: r.left + 'px', top: r.top + 'px',
                               width: r.width + 'px', height: r.height + 'px' });
  }

  const onMove = (e) => { if (!active) return; hovered = e.target; paint(hovered); };
  const onClick = (e) => {
    if (!active) return;
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    const el = e.target;
    deactivate();
    emit(el);
  };
  const onKey = (e) => {
    if (!active) return;
    if (e.key === 'Escape') {
      e.preventDefault(); deactivate();
      if (window.__mayaSkipBinding) window.__mayaSkipBinding('skipped');
    }
  };

  function activate(title, hint) {
    ensureChrome();
    active = true;
    hud.style.display = 'block';
    hud.innerHTML = `<span style="color:#fff">PICK:</span> ${title}` +
      (hint ? `<span style="color:#999;font-weight:400"> — ${hint}</span>` : '') +
      `<span style="float:right;color:#999;font-weight:400">click the element · Esc to skip</span>`;
  }

  function deactivate() {
    active = false;
    if (hud) hud.style.display = 'none';
    if (box) box.style.display = 'none';
  }

  // Bindings cannot carry a DOM handle (Playwright >=1.60), so the payload goes
  // over the binding and the element itself is stashed for handle-based checks
  // of selectors only Playwright can resolve (role=...).
  function emit(el) {
    window.__mayaPicked = el;
    if (window.__mayaPickBinding) window.__mayaPickBinding(build(el));
  }

  document.addEventListener('mousemove', onMove, true);
  document.addEventListener('click', onClick, true);
  document.addEventListener('keydown', onKey, true);

  // Reconnaissance: every interactive element actually on the page, each with
  // verified candidate selectors. This does NOT decide which element is the
  // serial box — a human still does that. It just stops them hunting blind.
  function inventory(limit) {
    const sel = 'input:not([type=hidden]), textarea, select, button, a[href], ' +
                '[role=button], [role=textbox], [role=searchbox], [role=table], ' +
                '[role=grid], table, [role=heading], h1, h2';
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      if (!(r.width || r.height)) continue;                    // skip hidden
      const built = build(el);
      const verified = built.candidates.filter(c => c.verified);
      out.push({
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type'),
        role: built.info.role,
        accessible_name: built.info.accessible_name,
        text: built.info.element_text.slice(0, 60),
        placeholder: el.getAttribute('placeholder'),
        best: verified.length ? { selector: verified[0].selector, strategy: verified[0].strategy } : null,
        unresolved: verified.length === 0,
      });
      if (out.length >= (limit || 80)) break;
    }
    return { url: location.href, title: document.title, count: out.length, elements: out };
  }

  window.__mayaPicker = {
    build, activate, deactivate, inventory,
    isActive: () => active,
    // Offline self-test drives the identical pick path without a human.
    pickBySelector: (sel) => {
      const el = document.querySelector(sel);
      if (!el) return false;
      deactivate();
      emit(el);
      return true;
    },
    probe: (sel) => {
      try {
        if (sel.startsWith('xpath=')) {
          const it = document.evaluate(sel.slice(6), document, null,
            XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
          return { count: it.snapshotLength };
        }
        return { count: document.querySelectorAll(sel).length };
      } catch (err) { return { count: 0, error: String(err && err.message || err) }; }
    },
  };
  window.__mayaActivate = activate;
})();
