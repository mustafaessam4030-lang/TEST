// End-to-end browser test of the real UI against a running server.
//
//   BASE=http://127.0.0.1:8765  SLOW=http://127.0.0.1:8766  FIXTURES=/path  OUT=/path  node tests/ui/e2e.mjs
//
// BASE  a real server with the sample PDFs uploaded (real extraction, OCR, retrieval, answers).
// SLOW  optional: a server started with EKA_GENERATOR=ollama pointing at tests/ui/slow_ollama.py,
//       a MOCK model that replies after a delay — used only for slow-response and Stop tests.
// Needs `npm i playwright` and a Chromium (PLAYWRIGHT_BROWSERS_PATH or `npx playwright install chromium`).
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = process.env.BASE || "http://127.0.0.1:8765";
const SLOW = process.env.SLOW || "";
const FX = process.env.FIXTURES || "/tmp/uifx";
const OUT = process.env.OUT || "/tmp/eka-shots";
const INBOX = process.env.INBOX || "";
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok: !!ok, detail }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`); };
const browser = await chromium.launch();
const errors = [];

async function page(width, height, opts = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 2, colorScheme: opts.scheme || "light",
    permissions: ["clipboard-read", "clipboard-write"], hasTouch: !!opts.touch, isMobile: !!opts.mobile });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(`${width}px: ${e.message}`));
  p.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(`${width}px: ${m.text()}`); });
  p.on("dialog", (d) => d.accept());
  await p.goto(opts.base || BASE);
  await p.waitForSelector("#sugs .ch-chip");
  return p;
}
const idle = (p) => p.waitForFunction(() => !document.querySelector(".msg.think"), null, { timeout: 30000 });
async function ask(p, text) { await p.fill("#q", text); await p.press("#q", "Enter"); await idle(p); }
const shot = (p, name) => p.screenshot({ path: `${OUT}/${name}.png` });
const noOverflow = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
const imgReady = (p) => p.waitForFunction(() => { const i = document.querySelector("#vImg"); return i.complete && i.naturalWidth > 0 && document.querySelector("#vLoad").hidden; }, null, { timeout: 20000 });

// ═════════ DESKTOP 1440 ═════════
{
  const p = await page(1440, 900);
  check("empty state shows grounded suggestions", (await p.locator("#sugs .ch-chip").count()) >= 1);
  await shot(p, "after-desktop-empty");
  check("send disabled while the composer is empty", await p.locator("#send").isDisabled());

  // streaming stages, captured as they arrive
  await p.evaluate(() => {
    window.__stages = [];
    new MutationObserver(() => { const l = document.querySelector(".th-label"); if (l && !window.__stages.includes(l.textContent)) window.__stages.push(l.textContent); })
      .observe(document.getElementById("log"), { subtree: true, childList: true, characterData: true });
  });
  await p.locator("#sugs .ch-chip").first().click();
  await idle(p);
  const stages = await p.evaluate(() => window.__stages);
  check("suggestion chip asks and answers", (await p.locator(".msg.bot .src").count()) > 0);
  check("streamed progress stages shown", stages.length >= 1, stages.join(" → "));
  check("URL becomes the conversation", /#\/c\/[0-9a-f]+/.test(p.url()));
  check("conversation appears in history under Today",
    (await p.locator("#hist .nav-g").first().textContent()) === "Today" && (await p.locator("#hist .nav-i.on").count()) === 1);

  await p.fill("#q", "line one");
  await p.press("#q", "Shift+Enter");
  await p.type("#q", "line two");
  check("Shift+Enter inserts a new line", (await p.inputValue("#q")) === "line one\nline two");
  await p.fill("#q", "");

  await ask(p, "What is the rated pressure of the HX-200?");
  const last = p.locator(".msg.bot").last();
  const leadText = await last.locator(".ans.lead").first().textContent();
  check("spec answer is a natural sentence with value and condition", leadText.includes("The HX-200's rated pressure is 350 bar (5076 psi), continuous duty."), leadText);
  check("no 'according to the documents' preamble", !/according to/i.test(await last.textContent()));
  check("older revision flagged separately", (await last.locator(".rev").count()) === 1 && (await last.locator(".rev").textContent()).includes("320 bar"));
  check("safety note preserved", (await last.locator(".safety").count()) === 1);
  await shot(p, "after-desktop-answer");

  await last.locator(".fb button", { hasText: "Copy" }).click();
  const clip = await p.evaluate(() => navigator.clipboard.readText());
  check("Copy puts the answer with sources on the clipboard", clip.includes("350 bar") && clip.includes("Sources:"));
  await last.locator('.fb button[aria-label="Helpful"]').click();
  await p.waitForTimeout(300);
  check("feedback toggles", (await last.locator('.fb button[aria-label="Helpful"]').getAttribute("aria-pressed")) === "true");
  const botsBefore = await p.locator(".msg.bot").count();
  await last.locator(".fb button.retry").click();
  await idle(p);
  check("Retry replaces the last answer", (await p.locator(".msg.bot").count()) === botsBefore);

  await last.locator("details.evx").first().locator("summary").click();
  check("sources disclosure lists source cards", (await p.locator(".msg.bot").last().locator(".sc").count()) >= 1);
  const pdfHref = await p.locator(".msg.bot").last().locator(".sc a.cbtn").first().getAttribute("href");
  check("Open PDF links to the cited page", /\/api\/documents\/[0-9a-f]+\/file#page=2$/.test(pdfHref), pdfHref);
  await p.locator(".msg.bot").last().locator("details.how summary").click();
  check("How this answer was found shows the retrieval trace", (await p.locator(".msg.bot").last().locator(".trace tbody tr").count()) >= 1);

  // clarification first, then options
  await ask(p, "What torque for the mounting flange bolts?");
  const clar = p.locator(".msg.bot").last();
  check("ambiguous question asks first, shows no values",
    (await clar.locator(".st").textContent()).includes("Needs clarification") && !(await clar.textContent()).match(/\b(180|210)\b/));
  check("clarification options are clickable", (await clar.locator(".opts .dl").count()) === 2);
  await shot(p, "after-desktop-clarify");
  await clar.locator(".opts .dl", { hasText: "HX-200" }).click();
  await idle(p);
  const chosen = p.locator(".msg.bot").last();
  const chosenText = await chosen.textContent();
  check("chosen model answered with its own value", /\b210\b/.test(chosenText) && !/\b180\b/.test(chosenText), chosenText.slice(0, 160));

  // procedure (long answer)
  await ask(p, "How do I replace the shaft seal on the HX-200?");
  const proc = p.locator(".msg.bot").last();
  check("procedure renders as an ordered list of 7 steps", (await proc.locator(".q ol li").count()) === 7);
  check("warning rendered as a warning block", (await proc.locator(".warn").count()) >= 1);
  check("procedure warning comes before the steps", await proc.evaluate((m) => {
    const w = m.querySelector(".warn"), steps = m.querySelector(".q ol");
    return !!(w && steps && (w.compareDocumentPosition(steps) & Node.DOCUMENT_POSITION_FOLLOWING));
  }));
  check("procedure has a labelled conceptual flow", (await proc.locator(".viz .flow li").count()) === 7 &&
    (await proc.locator(".viz .viz-n").textContent()).includes("not a manufacturer drawing"));
  const ordered = await proc.locator(".q", { has: p.locator("ol") }).first().evaluate((q) => [...q.children].map((c) => c.tagName).join(","));
  check("text after the steps stays after them", /OL,DIV/.test(ordered), ordered);

  // citation → panel at the cited page, chat scroll kept
  const anchorNow = () => p.evaluate(() => {
    const l = document.getElementById("log"), top = l.getBoundingClientRect().top;
    const el = [...l.children].find((c) => c.getBoundingClientRect().bottom > top + 8);
    return el ? { text: el.textContent.slice(0, 40), off: Math.round(el.getBoundingClientRect().top - top) } : null;
  });
  await proc.locator(".src").first().scrollIntoViewIfNeeded();
  await p.waitForTimeout(200);
  const scrollBefore = await anchorNow();
  await proc.locator(".src").first().click();
  await imgReady(p);
  await p.waitForTimeout(450);
  check("citation opens the document panel at the cited page", (await p.inputValue("#vPage")) === "3");
  check("cited passage shown above the page", !(await p.locator("#vQuote").isHidden()));
  check("layout does not shift: page unscrolled, panel and top bar fully visible", await p.evaluate(() =>
    document.scrollingElement.scrollTop === 0 && document.querySelector(".v-h").getBoundingClientRect().top >= 0 &&
    document.querySelector(".top").getBoundingClientRect().top >= 0));
  const chatW = await p.evaluate(() => document.getElementById("log").getBoundingClientRect().width);
  check("conversation keeps a comfortable width beside the panel", chatW >= 600, Math.round(chatW) + "px");
  await shot(p, "after-desktop-viewer");
  await p.click("#vNext"); await imgReady(p);
  check("next page", (await p.inputValue("#vPage")) === "4");
  await p.click("#vPrev"); await imgReady(p);
  check("previous page", (await p.inputValue("#vPage")) === "3");
  await p.fill("#vPage", "6"); await p.press("#vPage", "Enter"); await imgReady(p);
  check("page number input", (await p.inputValue("#vPage")) === "6");
  const w1 = await p.evaluate(() => document.getElementById("vImg").getBoundingClientRect().width);
  await p.click("#vIn"); await imgReady(p);
  const w2 = await p.evaluate(() => document.getElementById("vImg").getBoundingClientRect().width);
  check("zoom in enlarges the page", w2 > w1 * 1.2, `${Math.round(w1)} → ${Math.round(w2)}px`);
  await p.click("#vOut"); await imgReady(p);
  check("zoom out", (await p.textContent("#vZoom")) === "100%");
  await p.fill("#vQ", "relief valve");
  await p.waitForSelector(".v-hit");
  check("search in document lists matching pages", (await p.locator(".v-hit").count()) === 3);   // pp. 2, 4, 7
  await p.locator(".v-hit", { hasText: "p. 4" }).click(); await imgReady(p);
  check("clicking a match opens its page", (await p.inputValue("#vPage")) === "4");
  const gw1 = await p.evaluate(() => document.getElementById("viewer").getBoundingClientRect().width);
  const g = await p.locator("#grip").boundingBox();
  await p.mouse.move(g.x + 4, g.y + 300); await p.mouse.down(); await p.mouse.move(g.x - 120, g.y + 300, { steps: 6 }); await p.mouse.up();
  await p.waitForTimeout(100);
  const gw2 = await p.evaluate(() => document.getElementById("viewer").getBoundingClientRect().width);
  check("document panel is resizable", gw2 > gw1 + 80, `${Math.round(gw1)} → ${Math.round(gw2)}px`);
  await p.click("#vClose");
  await p.waitForTimeout(450);
  const scrollAfter = await anchorNow();
  check("closing the panel returns to the full-width conversation", !(await p.evaluate(() => document.getElementById("app").classList.contains("viewing"))));
  check("conversation position preserved across open/close",
    scrollBefore && scrollAfter && scrollBefore.text === scrollAfter.text && Math.abs(scrollBefore.off - scrollAfter.off) <= 4,
    JSON.stringify([scrollBefore, scrollAfter]));

  // unsupported
  await ask(p, "What is the warranty period of the HX-200?");
  check("unsupported question says it is not in the documents", (await p.locator(".msg.bot").last().locator(".st").textContent()).includes("Not in your documents")
    && (await p.locator(".msg.bot").last().textContent()).includes("couldn't verify"));

  // scope
  await p.click("#scopeBtn");
  await p.locator(".pop label.mi", { hasText: "CV-90" }).click();
  await p.keyboard.press("Escape");
  check("scope selector narrows to one document", (await p.textContent("#scopeTxt")).includes("CV-90"));
  await ask(p, "What torque for the mounting flange bolts?");
  check("scoped question answers from that document only", (await p.locator(".msg.bot").last().textContent()).includes("180"));
  await p.click("#scopeBtn");
  await p.locator(".pop label.mi", { hasText: "All documents" }).click();
  await p.keyboard.press("Escape");
  check("scope back to all documents", (await p.textContent("#scopeTxt")).startsWith("All documents"));

  // history: new chat, reopen, search, rename, delete
  const turns = await p.locator(".msg.me").count();
  const cid = p.url().split("#/c/")[1];
  await p.click("#newChat");
  check("New chat clears the conversation", (await p.locator(".msg").count()) === 0 && (await p.textContent("#title")) === "New chat");
  await p.locator(`#hist a[href="#/c/${cid}"]`).click();
  await p.waitForFunction((n) => document.querySelectorAll(".msg.me").length === n, turns);
  check("history item reopens the full conversation", (await p.locator(".msg.me").count()) === turns, String(turns));
  await p.keyboard.press("Control+Shift+O");
  await p.waitForFunction(() => document.getElementById("title").textContent === "New chat", null, { timeout: 3000 }).catch(() => {});
  check("Ctrl+Shift+O starts a new chat", (await p.textContent("#title")) === "New chat" && (await p.locator(".msg").count()) === 0);
  await ask(p, "What is the gear ratio of the CV-90?");
  await p.keyboard.press("Control+k");
  check("Ctrl+K focuses conversation search", await p.evaluate(() => document.activeElement.id === "histQ"));
  await p.fill("#histQ", "gear ratio");
  await p.waitForTimeout(500);
  check("conversation search filters history", (await p.locator("#hist .nav-i").count()) === 1);
  await p.fill("#histQ", "");
  await p.waitForTimeout(400);
  const row = p.locator("#hist .hist-row").first();
  await row.hover();
  await row.locator(".hist-more").click();
  await p.locator(".pop button", { hasText: "Rename" }).click();
  await p.keyboard.press("Control+a");
  await p.keyboard.type("CV-90 ratio check");
  await p.keyboard.press("Enter");
  await p.waitForFunction(() => document.querySelector("#hist .nav-i")?.textContent.includes("CV-90 ratio check"));
  check("rename conversation", true);
  check("title bar follows the rename", (await p.textContent("#title")) === "CV-90 ratio check");
  const before = await p.locator("#hist .nav-i").count();
  await p.locator("#hist .hist-row").first().hover();
  await p.locator("#hist .hist-row").first().locator(".hist-more").click();
  await p.locator(".pop button", { hasText: "Delete" }).click();
  await p.waitForFunction((n) => document.querySelectorAll("#hist .nav-i").length === n - 1, before);
  check("delete conversation", (await p.textContent("#title")) === "New chat");

  // sidebar + theme + settings
  await p.click("#collapseSide"); await p.waitForTimeout(400);
  check("sidebar collapses", await p.evaluate(() => document.getElementById("side").getBoundingClientRect().width < 2));
  await p.click("#showSide"); await p.waitForTimeout(400);
  check("sidebar expands", await p.evaluate(() => document.getElementById("side").getBoundingClientRect().width > 200));
  await p.keyboard.press("Control+.");
  await p.waitForTimeout(400);
  check("Ctrl+. toggles the sidebar", await p.evaluate(() => document.getElementById("app").classList.contains("side-closed")));
  await p.keyboard.press("Control+.");
  await p.click('.pf-th [data-theme-set="dark"]');
  // the theme cross-fades (a view transition), so it settles a frame or two later
  await p.waitForFunction(() => document.documentElement.dataset.theme === "dark", null, { timeout: 3000 }).catch(() => {});
  check("dark theme", (await p.evaluate(() => document.documentElement.dataset.theme)) === "dark");
  await p.click("#openSettings");
  check("settings open with honest retrieval mode", (await p.textContent("#setRetrieval")).includes("Semantic search"));
  await shot(p, "after-desktop-dark-settings");
  await p.click("[data-close-dialog]");
  check("settings close", !(await p.evaluate(() => document.getElementById("settings").open)));
  await p.click("#openSettings");
  await p.locator('#settings [data-theme-set="system"]').click();
  await p.waitForFunction(() => document.documentElement.dataset.theme === "light", null, { timeout: 3000 }).catch(() => {});
  await p.keyboard.press("Escape");
  check("Esc closes settings; System theme follows the device (light here)", !(await p.evaluate(() => document.getElementById("settings").open)) && (await p.evaluate(() => document.documentElement.dataset.theme)) === "light");

  // keyboard: "/" focuses composer, tab reaches send
  await p.click("#main .top-t");
  await p.keyboard.press("/");
  check("/ focuses the message box", await p.evaluate(() => document.activeElement.id === "q"));
  await p.fill("#q", "x");
  await p.keyboard.press("Tab");
  check("Tab moves from the message box to Send", await p.evaluate(() => document.activeElement.id === "send"));
  await p.fill("#q", "");

  // network failure → error state with Try again
  await p.route("**/api/chat/stream", (r) => r.abort());
  await ask(p, "What is the output speed of the CV-90?");
  check("network failure shows an error with Try again", (await p.locator(".err-b").count()) === 1);
  check("Luma: a failed request shows the error state, with the message visible", (await p.locator(".msg.bot:has(.err-b) .luma[data-state='error']").count()) === 1
    && (await p.locator(".err-b").isVisible()));
  await p.unroute("**/api/chat/stream");
  await p.locator(".err-b button").click();
  await idle(p);
  check("Try again recovers", (await p.locator(".msg.bot").last().textContent()).includes("45 rpm"));
  check("Luma: the retried answer does not inherit the error state", (await p.locator(".msg.bot .luma[data-state='error']").count()) === 0);

  // library
  await p.click("#navLib");
  await p.waitForSelector(".doc");
  check("library lists documents with statuses", (await p.locator(".doc .pill").count()) >= 6);
  check("failed document explains why", (await p.locator(".doc", { hasText: "corrupted" }).locator(".doc-e").textContent()).includes("corrupted"));
  await p.fill("#libQ", "CV-90");
  check("library filter", (await p.locator(".doc").count()) === 1);
  await p.fill("#libQ", "");
  await p.locator("#libFile").setInputFiles(`${FX}/not_a_pdf.txt`);
  await p.waitForSelector(".uploads li.bad");
  check("non-PDF upload is rejected with a reason", (await p.locator(".uploads li.bad").textContent()).includes("PDF"));
  await p.locator("#libFile").setInputFiles(`${FX}/ZX-7_long_title_wide_table.pdf`);
  await p.waitForFunction(() => [...document.querySelectorAll(".doc")].some((d) => d.textContent.includes("ZX-7") && d.textContent.includes("Ready")), null, { timeout: 120000 });
  check("upload processes to Ready with live status", true);
  const longTitle = p.locator(".doc", { hasText: "ZX-7" }).locator(".doc-t");
  check("long document title truncates without overflow", await longTitle.evaluate((e) => e.scrollWidth > e.clientWidth && getComputedStyle(e).textOverflow === "ellipsis") && await noOverflow(p));
  await p.locator(".doc", { hasText: "HX-200 Hydraulic Pump" }).first().locator('button[aria-label="Document options"]').click();
  await p.locator(".pop button", { hasText: "Page status" }).click();
  await p.waitForSelector(".doc-pages table");
  check("per-page status table", (await p.locator(".doc-pages tbody tr").count()) >= 3);
  await p.locator(".doc", { hasText: "CV-90" }).locator('button[aria-label="Document options"]').click();
  await p.locator(".pop button", { hasText: "Re-index" }).click();
  await p.waitForFunction(() => [...document.querySelectorAll(".doc")].some((d) => d.textContent.includes("CV-90") && d.textContent.includes("Ready")), null, { timeout: 60000 });
  check("re-index returns to Ready", true);
  if (INBOX) {
    check("library shows the watched PDF folder", (await p.textContent("#srcLoc")) === INBOX);
    fs.copyFileSync(`${FX}/FX-9_folder_datasheet.pdf`, `${INBOX}/FX-9_folder_datasheet.pdf`);
    fs.utimesSync(`${INBOX}/FX-9_folder_datasheet.pdf`, new Date(Date.now() - 60000), new Date(Date.now() - 60000));
    await p.click("#srcScan");
    await p.waitForFunction(() => [...document.querySelectorAll(".doc")].some((d) =>
      d.textContent.includes("PDF folder: FX-9_folder_datasheet.pdf") && d.textContent.includes("Ready")), null, { timeout: 60000 });
    check("a PDF dropped into the folder is indexed (Check folder now)", true);
    await p.waitForFunction(() => /1 PDF in the folder · 1 indexed/.test(document.getElementById("srcStat").textContent), null, { timeout: 30000 }).catch(() => {});
    check("folder status counts it", /1 PDF in the folder · 1 indexed/.test(await p.textContent("#srcStat")), await p.textContent("#srcStat"));
  }
  await shot(p, "after-desktop-library");
  await p.locator(".doc", { hasText: "corrupted" }).locator('button[aria-label="Document options"]').click();
  await p.locator(".pop button", { hasText: "Delete" }).click();
  await p.waitForFunction(() => ![...document.querySelectorAll(".doc")].some((d) => d.textContent.includes("corrupted")));
  check("delete document", true);
  await p.locator(".doc", { hasText: "ZX-7" }).locator("button", { hasText: "Open" }).click();
  await imgReady(p);
  check("library Open shows page 1 in the panel", (await p.inputValue("#vPage")) === "1");
  await p.keyboard.press("Escape");
  await p.waitForTimeout(400);
  check("Esc closes the document panel", !(await p.evaluate(() => document.getElementById("app").classList.contains("viewing"))));

  // large table answer
  await p.click("#navChat");
  if (INBOX) {
    await ask(p, "What is the maximum flow of the FX-9?");
    check("a folder document answers questions", (await p.locator(".msg.bot").last().textContent()).includes("85 L/min"));
  }
  await ask(p, "What is the wear limit of swing bearing bolt 12 on the ZX-7?");
  const zx = p.locator(".msg.bot").last();
  check("value from a 25-row table is answered with its exact value", (await zx.locator(".ans").first().textContent()).includes("30 mm"),
    await zx.locator(".ans").first().textContent());
  check("no horizontal page overflow with a wide table", await noOverflow(p));
  await shot(p, "after-desktop-widetable");

  // attachment from the composer
  await p.click("#newChat");
  await p.locator("#attachFile").setInputFiles(`${FX}/PX-3_attachment.pdf`);
  await p.waitForSelector(".att");
  check("attachment chip shows its name", (await p.locator(".att .nm").textContent()) === "PX-3_attachment.pdf");
  await p.waitForFunction(() => /Ready|Already/.test(document.querySelector(".att .s").textContent), null, { timeout: 60000 });
  check("attachment chip follows processing to Ready", true, await p.textContent(".att .s"));
  await shot(p, "after-desktop-attachment");
  await p.locator(".att .x").click();
  check("attachment chip can be dismissed", (await p.locator(".att").count()) === 0);
  await p.context().close();
}

// ═════════ DESKTOP DARK ═════════
{
  const p = await page(1440, 900, { scheme: "dark" });
  await ask(p, "What is the rated pressure of the HX-200?");
  await p.locator(".msg.bot").last().locator(".src").first().click();
  await imgReady(p);
  await p.waitForTimeout(450);
  check("dark: panel and top bar fully visible", await p.evaluate(() =>
    document.querySelector(".v-h").getBoundingClientRect().top >= 0 && document.querySelector(".top").getBoundingClientRect().top >= 0));
  await shot(p, "after-desktop-dark-viewer");
  // contrast of the text tokens on their surfaces, in both themes
  const ratios = await p.evaluate(() => {
    const lum = (c) => { const [r, g, b] = c.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
    const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
    const color = (v) => { const d = document.createElement("div"); d.style.color = `var(${v})`; document.body.append(d); const c = getComputedStyle(d).color; d.remove(); return c; };
    const out = {};
    for (const theme of ["light", "dark"]) {
      document.documentElement.dataset.theme = theme;
      for (const fg of ["--ink", "--mid", "--faint"]) for (const bg of ["--surface", "--bg"]) out[`${theme} ${fg} on ${bg}`] = +ratio(color(fg), color(bg)).toFixed(2);
    }
    document.documentElement.dataset.theme = "dark";
    return out;
  });
  const worst = Object.entries(ratios).sort((a, b) => a[1] - b[1])[0];
  check("text contrast ≥ 4.5:1 for every text token, both themes", worst[1] >= 4.5, `worst ${worst[0]} = ${worst[1]}:1`);
  await p.context().close();
}

// ═════════ TABLET 820 ═════════
{
  const p = await page(820, 1180, { touch: true });
  await ask(p, "What is the dry weight of the HX-200?");
  await p.locator(".msg.bot").last().locator(".src").first().click();
  await imgReady(p);
  await p.waitForTimeout(450);
  const overlay = await p.evaluate(() => getComputedStyle(document.getElementById("viewer")).position === "fixed");
  check("tablet: the document opens over the conversation, not beside it", overlay);
  await shot(p, "after-tablet-viewer");
  await p.click("#vBack");
  await p.waitForTimeout(400);
  check("tablet: back to the conversation", !(await p.evaluate(() => document.getElementById("app").classList.contains("viewing"))));
  check("tablet: no horizontal overflow", await noOverflow(p));
  await p.context().close();
}

// ═════════ PHONE 390 ═════════
{
  const p = await page(390, 844, { touch: true, mobile: true });
  check("phone: sidebar hidden behind a drawer", await p.evaluate(() => document.getElementById("side").getBoundingClientRect().right <= 1));
  const sugs = await p.$$eval("#sugs .ch-chip", (c) => c.map((x) => x.textContent));
  check("suggestion chips are unique", sugs.length > 0 && new Set(sugs).size === sugs.length, sugs.length + " chips");
  check("phone: 16px message box (no zoom on focus)", (await p.evaluate(() => getComputedStyle(document.getElementById("q")).fontSize)) === "16px");
  await shot(p, "after-mobile-empty");
  await ask(p, "What is the rated pressure of the HX-200?");
  await ask(p, "How do I replace the shaft seal on the HX-200?");
  check("phone: no horizontal overflow in a long answer", await noOverflow(p));
  check("phone: a long answer is shown from its question", await p.evaluate(() => {
    const me = [...document.querySelectorAll(".msg.me")].pop(), l = document.getElementById("log");
    const y = me.getBoundingClientRect().top - l.getBoundingClientRect().top;
    return y >= 0 && y < 60 && document.getElementById("jump").hidden;
  }));
  await ask(p, "Give me the key specifications of the CV-90");
  check("phone: table rows become labelled records", await p.evaluate(() => {
    const td = [...document.querySelectorAll(".dt td")].pop()?.closest("tr")?.children[1];
    return td && getComputedStyle(td).display === "block" && getComputedStyle(td, "::before").content.includes("Value");
  }));
  check("phone: no horizontal overflow with a table", await noOverflow(p));
  await shot(p, "after-mobile-answer");
  await p.locator(".msg.bot").last().locator(".src").first().click();
  await imgReady(p);
  await p.waitForTimeout(450);
  const full = await p.evaluate(() => { const r = document.getElementById("viewer").getBoundingClientRect(); return r.left <= 1 && r.width >= innerWidth - 1; });
  check("phone: citation opens a full-screen document view", full);
  await shot(p, "after-mobile-viewer");
  await p.click("#vBack");
  await p.waitForTimeout(400);
  check("phone: Back returns to the conversation", !(await p.evaluate(() => document.getElementById("app").classList.contains("viewing"))));
  await p.click("#showSide");
  await p.waitForTimeout(400);
  check("phone: drawer slides in", await p.evaluate(() => document.getElementById("side").getBoundingClientRect().left >= -1));
  await shot(p, "after-mobile-drawer");
  await p.click("#scrim", { position: { x: 370, y: 400 } });
  await p.waitForTimeout(400);
  check("phone: tapping outside closes the drawer", await p.evaluate(() => document.getElementById("side").getBoundingClientRect().right <= 1));
  await p.click("#showSide");
  await p.waitForTimeout(350);
  await p.click("#navLib");
  await p.waitForSelector(".doc");
  check("phone: library opens and the drawer closes", await p.evaluate(() => !document.getElementById("app").classList.contains("drawer")));
  await p.waitForTimeout(400);
  check("phone: library fits the screen", await noOverflow(p));
  await shot(p, "after-mobile-library");
  await p.context().close();
}

// ═════════ LUMA: identity, answer modes, figures, follow-ups ═════════
{
  const p = await page(1440, 900);
  check("Luma: product name and tagline", (await p.textContent("#brandName")) === "Luma" &&
    (await p.textContent("#brandTag")).includes("Mantrac") && (await p.title()).startsWith("Luma"));
  check("Luma: no unofficial organisation logo is shown", await p.locator("#orgLogo").isHidden());
  const sugs = await p.locator("#sugs .ch-chip").allTextContents();
  check("Luma: suggestions include a figure and a summary", sugs.some((x) => x.startsWith("Show me")) && sugs.some((x) => x.startsWith("Summarize")), sugs.join(" | "));

  await ask(p, "What is the oil capacity of the CV-90?");
  let last = p.locator(".msg.bot").last();
  const lead = await last.locator(".ans.lead").textContent();
  check("Luma: direct answer reads naturally", lead.startsWith("The CV-90's oil capacity is 2.4 L."), lead);
  check("Luma: the citation sits on the sentence", (await last.locator(".ans.lead .cite").count()) === 1);
  check("Luma: the source wording is kept for hover", ((await last.locator(".ans.lead span[title]").first().getAttribute("title")) || "").includes("Oil capacity | Value: 2.4 L"));
  check("Luma: follow-up suggestions offered", (await last.locator(".fu button").count()) >= 2);
  await last.locator(".fu button", { hasText: "Where is that shown" }).click();
  await idle(p);
  last = p.locator(".msg.bot").last();
  check("Luma: 'where is that shown' points to the page", (await last.locator(".loc").textContent()).includes("Page 1 of the CV-90"));
  check("Luma: follow-ups shown under the latest answer only", (await p.locator(".msg.bot:not(:last-child) .fu").evaluateAll((els) => els.every((e) => getComputedStyle(e).display === "none"))));
  await last.locator(".loc .cite").click();
  await imgReady(p);
  check("Luma: the location opens the cited page", (await p.inputValue("#vPage")) === "1");
  await p.click("#vClose");

  await ask(p, "What torque for the mounting flange bolts on the HX-200?");
  await p.locator(".msg.bot").last().locator(".fu button", { hasText: "Compare it with the CV-90" }).click();
  await idle(p);
  last = p.locator(".msg.bot").last();
  check("Luma: comparison table with both machines", (await last.locator(".dt tbody tr").count()) === 2 &&
    (await last.locator(".dt").textContent()).includes("210 N.m") && (await last.locator(".dt").textContent()).includes("180 N.m"));
  check("Luma: comparison sentence keeps both values", /180 N\.m.*while.*210 N\.m/.test(await last.locator(".ans.lead").textContent()));
  check("Luma: chart labelled conceptual", (await last.locator(".viz .badge.concept").count()) === 1 &&
    (await last.locator(".viz .viz-n").textContent()).startsWith("Conceptual chart drawn from the cited values"));
  await shot(p, "luma-desktop-compare");

  await ask(p, "Show me the hydraulic circuit");
  last = p.locator(".msg.bot").last();
  await p.waitForFunction(() => { const i = [...document.querySelectorAll(".fig img")].pop(); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 20000 });
  check("Luma: original figure shown from the page", (await last.locator(".fig figcaption").textContent()).includes("Figure 1 HX-200 hydraulic circuit"));
  check("Luma: figure marked as original, not generated", (await last.locator(".fig .badge").textContent()).includes("Original figure"));
  check("Luma: the figure comes before its explanation", await last.evaluate((m) => {
    const f = m.querySelector(".fig"), t = m.querySelector(".ans");
    return !!(f && t && (f.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING));
  }));
  await last.locator(".fig-i").click();
  await imgReady(p);
  check("Luma: clicking the figure opens its page", (await p.inputValue("#vPage")) === "7");
  await shot(p, "luma-desktop-figure");
  await p.click("#vClose");

  await ask(p, "Summarize the HX-200 manual");
  last = p.locator(".msg.bot").last();
  check("Luma: summary outlines the manual's sections", (await last.locator(".outline li").count()) >= 6 &&
    (await last.locator(".outline").textContent()).includes("Hydraulic Circuit"));
  await ask(p, "What changed between Rev A and Rev B of the HX-200 manual?");
  last = p.locator(".msg.bot").last();
  const rev = await last.locator(".dt").textContent();
  check("Luma: revision changes table", rev.includes("320 bar (4641 psi)") && rev.includes("350 bar (5076 psi)"));

  const [chooser] = await Promise.all([p.waitForEvent("filechooser", { timeout: 5000 }).catch(() => null), p.click("#navUpload")]);
  check("Luma: 'Upload documents' in the sidebar opens the file picker", !!chooser);
  await p.context().close();
}
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, reducedMotion: "reduce" });
  const p = await ctx.newPage();
  await p.goto(BASE);
  await p.waitForSelector("#sugs .ch-chip");
  await ask(p, "What is the oil capacity of the CV-90?");
  const heroAnim = await p.evaluate(() => getComputedStyle(document.querySelector(".ch-e-luma .lm-fig")).animationName);
  check("Luma: reduced motion stops the mascot's motion", heroAnim === "none", heroAnim);
  const dur = await p.evaluate(() => getComputedStyle(document.querySelector(".msg.bot")).animationDuration);
  check("Luma: reduced motion turns animations off", parseFloat(dur) < 0.05, dur);
  await ctx.close();
}

// ═════════ SLOW MODEL (MOCK) ═════════
if (SLOW) {
  const p = await page(1440, 900, { base: SLOW });
  await p.fill("#q", "What is the dry weight of the HX-200?");
  await p.press("#q", "Enter");
  await p.waitForFunction(() => /Writing the answer/.test(document.querySelector(".th-label")?.textContent || ""), null, { timeout: 15000 });
  check("slow model: loading indicator names the real stage", true, await p.textContent(".th-label"));
  check("slow model: send turns into Stop", (await p.getAttribute("#send", "aria-label")) === "Stop");
  await shot(p, "after-desktop-thinking");
  await p.click("#send");
  await p.waitForSelector(".ch-done");
  check("Stop ends the response and says nothing was saved", (await p.textContent(".ch-done")).includes("not saved"));
  await p.waitForTimeout(800);
  check("stopped question is not in history", (await p.locator("#hist .nav-i").count()) === 0);
  await ask(p, "What is the dry weight of the HX-200?");
  const a = p.locator(".msg.bot").last();
  check("slow model: answer arrives after the wait, cited", (await a.locator(".src").count()) >= 1 && (await a.textContent()).includes("48 kg"));
  await a.locator("details.how summary").click();
  check("slow model: method shows the model used", (await a.locator(".how").textContent()).includes("ollama:qwen2.5:7b-instruct"));
  await p.context().close();
}

// ═════════ LUMA, THE MASCOT: states follow the real request ═════════
// SLOW runs the mock model (6 s) and EKA_DEBUG_STAGE_DELAY_MS=600, so every stage is long
// enough to observe. States are recorded from the DOM as they change.
if (SLOW) {
  const p = await page(1440, 900, { base: SLOW });
  const record = () => p.evaluate(() => {
    window.__luma = []; window.__labels = []; window.__doneAfterAnswer = null;
    const log = document.getElementById("log");
    window.__obs?.disconnect();
    window.__obs = new MutationObserver((ms) => {
      // rebuild every state each live mascot passed through, even several within one batch:
      // a record's new value is the next record's old value (or the current value for the last)
      const push = (s, el) => {
        if (!s || s === window.__luma[window.__luma.length - 1]) return;
        window.__luma.push(s);
        if (s === "done") window.__doneAfterAnswer = !!el.closest(".msg.bot:not(.think)")?.querySelector(".bub .fb");
      };
      const attrs = ms.filter((m) => m.type === "attributes" && m.target.classList?.contains("luma-live"));
      for (const m of ms) {
        if (m.type === "childList") m.addedNodes.forEach((n) => {
          if (n.nodeType !== 1) return;
          const l = n.matches(".luma-live") ? n : n.querySelector(".luma-live");
          if (!l) return;
          const firstChange = attrs.find((x) => x.target === l);
          push(firstChange ? firstChange.oldValue : l.dataset.state, l);
        });
      }
      attrs.forEach((m, i) => {
        const next = attrs.slice(i + 1).find((x) => x.target === m.target);
        push(next ? next.oldValue : m.target.dataset.state, m.target);
      });
      const lab = document.querySelector(".msg.think .th-label");
      if (lab && lab.textContent !== window.__labels[window.__labels.length - 1]) window.__labels.push(lab.textContent);
    });
    window.__obs.observe(log, { subtree: true, childList: true, attributes: true, attributeOldValue: true, attributeFilter: ["data-state"] });
  });
  const hero = p.locator(".ch-e-luma");
  check("Luma: idle on the welcome screen, floating", (await hero.getAttribute("data-state")) === "idle" &&
    (await hero.locator(".lm-fig").evaluate((e) => getComputedStyle(e).animationName)) === "lmFloat");
  await record();
  await p.fill("#q", "What is the dry weight of the HX-200?");
  await p.press("#q", "Enter");
  await p.waitForSelector(".msg.think .luma[data-state='searching']", { timeout: 10000 });
  await shot(p, "luma-state-searching");
  await p.waitForSelector(".msg.think .luma[data-state='writing']", { timeout: 15000 });
  await p.waitForFunction(() => /Writing the answer/.test(document.querySelector(".th-label")?.textContent || ""), null, { timeout: 15000 });
  await shot(p, "luma-state-writing");
  await idle(p);
  await p.waitForTimeout(1300);   // "done" plays once (0.95 s), then rests
  const seq = await p.evaluate(() => window.__luma);
  check("Luma: states follow the real pipeline, in order",
    JSON.stringify(seq) === JSON.stringify(["waiting", "thinking", "searching", "reading", "writing", "analyzing", "done", "static"]), seq.join(" → "));
  const labels = await p.evaluate(() => window.__labels);
  check("Luma: each state comes with the server's own status label", ["Searching", "Reading", "Preparing your answer", "Writing the answer with", "Checking every citation"]
    .every((x) => labels.some((l) => l.startsWith(x))), labels.join(" | "));
  check("Luma: 'done' only after the answer was on screen", (await p.evaluate(() => window.__doneAfterAnswer)) === true);
  check("Luma: rests as a still beside the finished answer", (await p.locator(".msg.bot").last().locator(".m-av.luma").getAttribute("data-state")) === "static");
  check("Luma: the welcome mascot is paused while hidden", await hero.evaluate((e) => e.hasAttribute("data-paused")));

  // a follow-up starts fresh: nothing from the previous request carries over
  await record();
  await p.fill("#q", "And its rated flow?");
  await p.press("#q", "Enter");
  await p.waitForSelector(".msg.think .luma", { timeout: 5000 });
  const first = (await p.evaluate(() => window.__luma))[0];
  check("Luma: a follow-up starts from 'waiting', not a stale state", first === "waiting", first);
  await idle(p);
  await p.waitForTimeout(1300);
  check("Luma: every finished answer has Luma beside it", await p.evaluate(() =>
    [...document.querySelectorAll(".msg.bot")].every((m) => m.querySelector(".m-av.luma"))));
  check("Luma: no mascot left mid-task after the answers", (await p.locator("#log .luma-live:not([data-state='static'])").count()) === 0);

  // cancelling: the working mascot goes away with the cancelled request
  await p.fill("#q", "What is the displacement of the HX-200?");
  await p.press("#q", "Enter");
  await p.waitForSelector(".msg.think .luma[data-state='writing']", { timeout: 15000 });
  await p.click("#send");
  await p.waitForSelector(".ch-done");
  check("Luma: Stop removes the working mascot", (await p.locator("#log .msg.think").count()) === 0 &&
    (await p.locator("#log .luma-live:not([data-state='static'])").count()) === 0);
  // citations still work with Luma beside the answer
  await p.locator(".msg.bot").first().locator(".cite").first().click();
  await imgReady(p);
  check("Luma: citation clicks still open the page", (await p.inputValue("#vPage")) === "2");
  await p.keyboard.press("Escape");
  await p.context().close();
}

check("no JavaScript errors in any run", errors.length === 0, errors.slice(0, 3).join(" | "));
await browser.close();
const failed = results.filter((r) => !r.ok);
fs.writeFileSync(`${OUT}/e2e-results.json`, JSON.stringify(results, null, 2));
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
