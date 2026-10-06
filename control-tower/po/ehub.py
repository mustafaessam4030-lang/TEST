"""
eHub discovery — the business process, step by step, and nothing else.

    eHub shipment list (BU view, the Hub's own "Under Clearance" filter)
      → a record whose Status is exactly "Under Clearance"      (else SKIPPED)
      → Manage → the record's details
      → the Documents section
      → the document whose name starts with "Bill Entry"        (else DOCUMENT_NOT_FOUND)
      → the identifier after "Bill Entry"                       (several → NEEDS_REVIEW)
      → download that document (the eHub's own sign-in)
      → the PO pipeline, given the identifier

Every step is written to a TRAIL — what was looked at, what was found, what
was decided and why — kept on the job and shown in its drawer, so the job
can always be explained from its own record.

THE BOUNDARY. Three functions touch the eHub page and nothing else does:

    find_record(page, reference, skip)   which row, and its own values
    open_manage(page, row)               click Manage on that row
    (the Documents section, Bill Entry and the download are read from the
     page the Manage click opened, in this module)

The production pair reuses the ETA automation's existing, tested list
navigation (update_eta.ensure_filtered_page, find_row_by_bol,
build_header_map, click_manage_in_view), read-only, in this job's own process
and browser — the ETA run is not touched, blocked or shared.

What has been verified where: the list navigation runs on the real eHub
every ETA run. The Manage page's Documents section has NOT yet been seen by
this code on the real eHub — run `python -m po ehub-probe` on the worker PC.
"""

import re
import time
from datetime import datetime
from urllib.parse import unquote, urljoin, urlparse

from .pipeline import SourceError

REQUIRED_STATUS = "Under Clearance"
# "Bill Entry" as eHub actually writes it. The real Manage page (screenshot,
# 2026-10-06) lists "BillofEntry_40926696852 (1) (1).pdf": no spaces, "of"
# in the middle, an underscore before the number. "Bill Entry", "Bill of
# Entry", "Bill_Entry" and "BillofEntry" are all the same document type.
BILL_ENTRY = re.compile(r"^\s*bill[\s_\-]*(?:of[\s_\-]*)?entry(?![a-z])", re.I)
# The identifier: the first token after it (an optional "No." / "Number" /
# "#" between) with a digit in it. The extension and any number of browser
# copy suffixes — " (1)", " (1) (1)" — are not part of it.
IDENTIFIER = re.compile(
    r"^\s*bill[\s_\-]*(?:of[\s_\-]*)?entry[\s_\-:.#]*(?:(?:no|number|nr)\b[\s_\-:.#]*)?"
    r"(?P<id>[A-Za-z0-9][A-Za-z0-9/\-]*?)(?:\s*\(\d+\))*\s*(?:\.[A-Za-z0-9]{2,5})?\s*$", re.I)
# Controls on the Manage page the PO automation must never press: they change
# the eHub record. Only the selected document's own entry is ever clicked.
NEVER_CLICK = re.compile(r"\b(save|complete|correction|upload|submit|delete|remove|approve|"
                         r"reject|cancel|choose files?)\b", re.I)
TRANSIENT = ("timeout", "net::", "err_", "connection", "econnreset", "502", "503", "504")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def status_ok(text):
    """Exactly "Under Clearance" — spacing normalised, nothing else forgiven."""
    return " ".join(str(text or "").split()) == REQUIRED_STATUS


def identifier_of(name):
    """The identifier after "Bill Entry" in a document's name, or None."""
    if not BILL_ENTRY.match(str(name or "")):
        return None
    m = IDENTIFIER.match(str(name).strip())
    if not m or not re.search(r"\d", m.group("id")):
        return None
    return m.group("id").strip("-/")


def select(entries):
    """
    The Bill Entry document, by a deterministic rule — or a reason to stop.

    -> (selected | None, candidates, rule, outcome)
       outcome: "selected" | "none" | "review"
    """
    candidates = [dict(e, identifier=identifier_of(e["name"])) for e in entries
                  if BILL_ENTRY.match(e.get("name") or "")]
    if not candidates:
        return None, [], None, "none"
    ids = {c["identifier"] for c in candidates}
    if None in ids:
        return None, candidates, ("a document named 'Bill Entry' carries no identifier after "
                                  "it; nothing is guessed"), "review"
    if len(ids) > 1:
        return None, candidates, ("{0} Bill Entry documents carry different identifiers ({1}); "
                                  "there is no safe rule to choose — a person decides".format(
                                      len(candidates), ", ".join(sorted(ids)))), "review"
    if len(candidates) == 1:
        return candidates[0], candidates, "the only document whose name starts with 'Bill Entry' (eHub writes 'BillofEntry_')", \
            "selected"
    return candidates[0], candidates, ("{0} documents carry the same identifier {1}; the first "
                                       "listed in the Documents section was taken (the PDF is "
                                       "then checked against that identifier)".format(
                                           len(candidates), candidates[0]["identifier"])), \
        "selected"


def _kind_of(error):
    text = str(error).lower()
    return "transient" if any(t in text for t in TRANSIENT) else "permanent"


# ── THE PAGE: Documents section ─────────────────────────────────────────

DOCUMENTS_JS = r"""() => {
  const label = /^\s*documents?\s*(\(\d+\))?\s*$/i;
  const own = (el) => Array.from(el.childNodes).filter(n => n.nodeType === 3)
                         .map(n => n.textContent).join(' ').trim() || (el.innerText || '').trim();
  // The section's heading — a heading-like element first; a tab or link
  // labelled "Documents" only when nothing else is.
  const candidates = (sel) => Array.from(document.querySelectorAll(sel))
      .filter(el => label.test(own(el)) && (el.innerText || '').trim().length < 40);
  let heads = candidates('h1,h2,h3,h4,h5,h6,legend,caption,th,label,b,strong');
  if (!heads.length) heads = candidates('span,div,td,p');
  if (!heads.length) heads = candidates('a,li,button');
  const name = (e) => (e.innerText || e.value || e.title || e.getAttribute('download') || '').trim();
  const FILE = /\.[a-z0-9]{2,5}\s*$/i, BILL = /^bill[\s_\-]*(of[\s_\-]*)?entry/i;
  // A clickable entry, named by its own text — or, when its own text is a
  // "View" / "Download" button, by the document name in ITS OWN ROW. The
  // row is found by its content, not its tag (eHub draws rows as blocks,
  // not <tr>): the nearest ancestor holding exactly one file name. One that
  // holds two has gone past the row, and names nothing.
  const leaves = (root) => Array.from(root.querySelectorAll('*'))
      .filter(c => !c.children.length).map(c => (c.innerText || c.value || '').trim())
      .filter(t => t && t.length < 200);
  const rowName = (e) => {
    let box = e.parentElement;
    for (let up = 0; up < 6 && box; up++, box = box.parentElement) {
      const files = Array.from(new Set(leaves(box).filter(t => FILE.test(t) || BILL.test(t))));
      if (files.length === 1) return files[0];
      if (files.length > 1) return '';
    }
    return '';
  };
  const collect = (root) => Array.from(root.querySelectorAll(
      'a, button, input[type=button], input[type=submit], [onclick]'))
      .map((e, i) => {
        const own = name(e), inRow = rowName(e);
        const named = BILL.test(own) || FILE.test(own);
        // A mark on the element itself, so the click lands on THIS row's
        // control — never on the first "Download" of the page.
        e.setAttribute('data-ata-doc', String(i));
        return {name: (named || !inRow ? own : inRow).slice(0, 200),
                text: own.slice(0, 80), href: e.getAttribute('href') || '',
                tag: e.tagName.toLowerCase(), index: i, id: e.id || '', clickable: true,
                mark: String(i)};
      })
      .filter(x => x.name);
  for (const h of heads){
    let box = h;
    for (let up = 0; up < 6 && box; up++){
      box = box.parentElement;
      if (!box) break;
      const found = collect(box).filter(x => /bill[\s_\-]*(of[\s_\-]*)?entry|\.pdf\b/i
                                                .test(x.name + ' ' + x.href));
      if (found.length) return {found: true, label: own(h), scope: 'documents section',
                                entries: collect(box)};
    }
  }
  return {found: heads.length > 0, label: heads.length ? own(heads[0]) : null,
          scope: heads.length ? 'documents section (no document listed in it)' : null,
          entries: heads.length ? [] : collect(document.body)};
}"""


def open_documents(page):
    """Click a 'Documents' tab or link if there is one, then read the section."""
    label = re.compile(r"^\s*documents?\s*(\(\d+\))?\s*$", re.I)
    clicked = False
    # The innermost clickable first: clicking the middle of a wide <li> can
    # miss the link inside it.
    for selector in ("a", "button, input[type=button], input[type=submit]", "[role=tab]",
                     "li, span, div"):
        for frame in page.frames:
            try:
                tab = frame.locator(selector).filter(has_text=label).first
                if tab.count() and tab.is_visible():
                    tab.click(timeout=5000)
                    page.wait_for_timeout(600)
                    clicked = True
                    break
            except Exception:
                continue
        if clicked:
            break
    best = None
    for frame in page.frames:
        try:
            section = frame.evaluate(DOCUMENTS_JS)
        except Exception:
            continue
        section["frame_url"] = frame.url
        section["frame"] = frame
        if section.get("found") and section.get("entries"):
            return section
        best = best or section
    return best or {"found": False, "entries": [], "scope": None}


def _entries(section):
    """Distinct document entries, in page order: a name and how to fetch it."""
    seen, out = set(), []
    for e in section.get("entries") or []:
        name = e["name"]
        href = e.get("href") or ""
        if not name or (not e.get("clickable") and not href):
            continue
        # A document has a file name or a link to one; "Upload", "Save" and
        # the like are controls, not documents.
        looks_like_file = bool(re.search(r"\.[A-Za-z0-9]{2,5}\s*$", name)) or (
            href and not href.startswith(("javascript:", "#")) and
            "." in unquote(urlparse(href).path).rsplit("/", 1)[-1]) or BILL_ENTRY.match(name)
        if not looks_like_file or NEVER_CLICK.search(e.get("text") or name):
            continue
        filename = unquote(urlparse(href).path).rsplit("/", 1)[-1] if href and \
            not href.startswith(("javascript:", "#")) else ""
        # The name eHub shows; the link's own file name only when it shows none.
        shown = name if (name and name.lower() not in ("view", "download", "open")) or \
            not filename else filename
        key = (shown.lower(), href)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": shown, "href": href, "id": e.get("id"), "tag": e.get("tag"),
                    "index": e.get("index"), "text": e.get("text"), "mark": e.get("mark")})
    return out


def download(page, frame, entry):
    """
    The selected document's bytes, through this browser's eHub session:
    a plain link is fetched; a postback (WebForms LinkButton) or a script
    button is clicked and its download or popup is captured.
    """
    href = entry.get("href") or ""
    if href and not href.startswith(("javascript:", "#")):
        url = urljoin(frame.url, href)
        response = page.context.request.get(url, timeout=60000)
        if response.status >= 500:
            raise SourceError("eHub answered {0} for the document".format(response.status),
                              "transient")
        if response.status >= 400:
            raise SourceError("eHub answered {0} for the document".format(response.status),
                              "permanent")
        return response.body(), url, "link", response.headers.get("content-disposition")
    if NEVER_CLICK.search(entry.get("text") or "") or NEVER_CLICK.search(entry.get("name") or ""):
        raise SourceError("refused to click '{0}': it is a control that changes the eHub record, "
                          "not a document".format(entry.get("text") or entry["name"]), "permanent")
    # THIS row's own control: the mark set when the section was read, else
    # its id. Never "the first element saying Download".
    if entry.get("mark") is not None:
        locator = frame.locator("[data-ata-doc='{0}']".format(entry["mark"]))
    elif entry.get("id"):
        locator = frame.locator("[id='{0}']".format(entry["id"]))
    else:
        locator = frame.get_by_text(entry.get("text") or entry["name"], exact=True).first
    try:
        with page.expect_download(timeout=30000) as info:
            locator.click(timeout=10000)
        dl = info.value
        path = dl.path()
        with open(path, "rb") as handle:
            data = handle.read()
        return data, dl.url, "download", 'attachment; filename="{0}"'.format(
            dl.suggested_filename)
    except SourceError:
        raise
    except Exception as first_error:
        try:
            with page.context.expect_page(timeout=15000) as pop:
                locator.click(timeout=10000)
            popup = pop.value
            popup.wait_for_load_state("load", timeout=30000)
            response = page.context.request.get(popup.url, timeout=60000)
            data = response.body()
            popup.close()
            return data, popup.url, "popup", response.headers.get("content-disposition")
        except Exception:
            raise SourceError("the Bill Entry document could not be downloaded: {0}".format(
                str(first_error)[:200]), _kind_of(first_error))


# ── PROVENANCE: REAL only when the real eHub was observed ────────────────

def ehub_host():
    """
    The real eHub's host name — fixed (intelligence.verification, EHUB_HOST),
    not read from whatever URL the automation was pointed at: a stand-in the
    navigation is aimed at can never become the "real" host.
    """
    from intelligence import verification as V
    return V.ehub_host()


def provenance(navigation_real, observed, complete):
    """
    REAL / VERIFIED only when ALL of these hold:
      * the production navigation ran (find_in_ehub + open_manage_in_ehub),
      * every page and the document were served by the real eHub host,
      * the discovery reached the downloaded Bill Entry document.
    Anything else is TEST or UNVERIFIED. Nothing a caller passes can make a
    stand-in REAL: it is decided from the hosts the browser actually saw.
    """
    real_host = ehub_host()
    seen = {k: v for k, v in observed.items() if v}
    on_ehub = bool(seen) and bool(real_host) and all(h == real_host for h in seen.values())
    source = "REAL" if (navigation_real and on_ehub) else "TEST"
    if source != "REAL":
        why = ("stand-in navigation, not the eHub list" if not navigation_real else
               "pages were served by {0}, not {1}".format(
                   ", ".join(sorted(set(seen.values()))) or "nothing", real_host))
    elif not complete:
        why = "observed on the real eHub, but the discovery did not reach the Bill Entry document"
    else:
        why = "observed in the real eHub browser session, list to download"
    return {"source": source,
            "verification": "VERIFIED" if source == "REAL" and complete else "UNVERIFIED",
            "ehub_host": real_host, "observed_hosts": seen,
            "navigation": "production" if navigation_real else "stand-in", "why": why}


def _host_of(url):
    try:
        return urlparse(url or "").hostname
    except Exception:
        return None


# ── THE SOURCE ───────────────────────────────────────────────────────────

class EHubSource(object):
    """
    The PO pipeline's source for the real business process. `find_record`
    and `open_manage` are the only two calls that navigate the list; the
    production pair is `find_in_ehub` / `open_manage_in_ehub` below.
    """

    def __init__(self, page, find_record, open_manage, skip=None):
        self.page = page
        self.find_record = find_record
        self.open_manage = open_manage
        self.skip = set(skip or ())

    def fetch(self, reference):
        trail = {"started": now(), "steps": [], "required_status": REQUIRED_STATUS,
                 "navigation_path": []}
        navigation_real = self.find_record is find_in_ehub and \
            self.open_manage is open_manage_in_ehub
        observed = {}

        def seal(complete):
            trail["provenance"] = provenance(navigation_real, observed, complete)

        def step(name, ok, **detail):
            trail["steps"].append(dict({"step": name, "ok": ok, "at": now()}, **detail))

        def stop(message, kind, **extra):
            error = SourceError(message, kind, extra.pop("candidates", None))
            seal(False)
            error.trail = trail
            for k, v in extra.items():
                setattr(error, k, v)
            return error

        # 1. the eHub record
        try:
            row, looked = self.find_record(self.page, reference, self.skip)
        except SourceError as error:
            seal(False)
            error.trail = trail
            raise
        except Exception as error:
            step("ehub_record", False, error=str(error)[:200],
                 evidence=capture(self.page, "ehub_record"))
            raise stop("the eHub shipment list could not be read: {0}".format(str(error)[:200]),
                       _kind_of(error))
        trail["looked_at"] = looked
        try:
            trail["list_url"] = self.page.url
            observed["list"] = _host_of(self.page.url)
            trail["navigation_path"].append({"page": "shipment list", "url": self.page.url})
        except Exception:
            pass
        if row is None:
            step("ehub_record", False, reference=reference, looked=len(looked),
                 evidence=capture(self.page, "ehub_record"))
            if reference and looked and looked[0].get("view") == SEARCH_VIEW:
                raise stop("{0} was searched in eHub's Shipments List (BOL/AWB Number) and no "
                           "row came back for it. It was not opened.".format(reference),
                           "not_found")
            if reference:
                raise stop("{0} is not listed under eHub's 'Under Clearance' filter on view "
                           "{1} — its status is not Under Clearance, or it is not in eHub. "
                           "It was not opened.".format(reference, (looked[0] if looked else {})
                                                        .get("view", "BU")), "skipped")
            raise stop("no Under Clearance record is waiting in eHub (every listed one is "
                       "already processed or not Under Clearance)", "not_found")
        trail["ehub_record"] = row
        step("ehub_record", True, bol_awb=row.get("bol_awb"), carrier=row.get("carrier"),
             table_page=row.get("table_page"), view=row.get("view"))

        # 2. the status rule — exact, checked before anything is opened
        ok = status_ok(row.get("status"))
        trail["clearance"] = {"required": REQUIRED_STATUS, "found": row.get("status"), "ok": ok}
        step("clearance_status", ok, found=row.get("status"), required=REQUIRED_STATUS)
        if not ok:
            raise stop("{0} has status '{1}', not exactly '{2}': skipped, Manage was not "
                       "opened.".format(row.get("bol_awb"), row.get("status"), REQUIRED_STATUS),
                       "skipped", row=row)

        # 3. Manage → the record's details
        try:
            opened = self.open_manage(self.page, row) or {}
        except Exception as error:
            step("manage", False, error=str(error)[:200], evidence=capture(self.page, "manage"))
            raise stop("Manage could not be opened for {0}: {1}".format(
                row.get("bol_awb"), str(error)[:200]), _kind_of(error), row=row)
        try:
            opened.setdefault("url", self.page.url)
        except Exception:
            pass
        trail["manage"] = opened
        observed["manage"] = _host_of(opened.get("url"))
        trail["navigation_path"].append({"page": "Manage", "url": opened.get("url"),
                                         "row": row.get("bol_awb")})
        step("manage", True, url=opened.get("url"))

        # 4. the Documents section
        section = open_documents(self.page)
        entries = _entries(section)
        trail["navigation_path"].append({"page": "Documents", "scope": section.get("scope")})
        trail["documents"] = {"found": bool(section.get("found")), "label": section.get("label"),
                              "scope": section.get("scope"),
                              "entries": [e["name"] for e in entries][:30]}
        step("documents_section", bool(section.get("found")), scope=section.get("scope"),
             count=len(entries))
        if not section.get("found"):
            trail["documents"]["evidence"] = capture(self.page, "documents")
            raise stop("Manage opened for {0}, but its details have no Documents section; "
                       "nothing is guessed.".format(row.get("bol_awb")), "not_found", row=row)

        # 5. the Bill Entry document and its identifier
        selected, candidates, rule, outcome = select(entries)
        trail["bill_entry"] = {"candidates": [{"name": c["name"], "identifier": c["identifier"]}
                                              for c in candidates],
                               "selected": selected["name"] if selected else None,
                               "identifier": selected["identifier"] if selected else None,
                               "rule": rule}
        if outcome == "none":
            step("bill_entry", False, documents=len(entries),
                 evidence=capture(self.page, "bill_entry"))
            raise stop("Manage → Documents for {0} lists {1} document(s), none whose name starts "
                       "with 'Bill Entry' (or 'BillofEntry'){2}. No identifier is invented.".format(
                           row.get("bol_awb"), len(entries),
                           " (" + ", ".join(e["name"] for e in entries[:6]) + ")" if entries
                           else ""), "no_bill_entry", row=row)
        if outcome == "review":
            step("bill_entry", False, rule=rule)
            raise stop(rule[0].upper() + rule[1:] + ".", "review", row=row,
                       candidates=[c["name"] for c in candidates])
        step("bill_entry", True, filename=selected["name"], rule=rule)
        step("identifier", True, identifier=selected["identifier"],
             source="the document name after 'Bill Entry'")

        # 6. download it
        try:
            data, url, method, disposition = download(self.page, section["frame"], selected)
        except SourceError as error:
            step("download", False, error=str(error)[:200])
            seal(False)
            error.trail = trail
            raise
        if not data.startswith(b"%PDF"):
            step("download", False, error="not a PDF")
            raise stop("the Bill Entry document is not a PDF", "permanent", row=row)
        filename = selected["name"]
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition or "", re.I)
        served = unquote(m.group(1)) if m else None
        trail["download"] = {"method": method, "url": url, "served_filename": served,
                             "bytes": len(data), "link": selected.get("href") or None,
                             "element_id": selected.get("id") or None}
        observed["download"] = _host_of(url)
        trail["navigation_path"].append({"page": "Bill Entry document", "url": url,
                                         "filename": filename})
        step("download", True, method=method, bytes=len(data), served_filename=served)
        seal(True)
        hub = dict(row, identifier=selected["identifier"], bill_entry=filename)
        return {"data": data, "filename": filename, "url": url, "origin": "ehub", "hub": hub,
                "identifier": selected["identifier"], "trail": trail}


# ── PRODUCTION NAVIGATION (the ETA automation's own, read-only) ─────────

def choose(rows, reference=None, skip=()):
    """
    THE ROW RULE, one place for the real eHub and any stand-in:
    the row whose BOL/AWB is `reference`; or, with none, the first row whose
    Status is exactly Under Clearance and that is not already handled.
    Returns (row | None, looked_at) — every row passed over, and why.
    """
    from .extract import normal_reference
    want = normal_reference(reference) if reference else None
    handled = {normal_reference(s) for s in skip}
    looked = []
    for row in rows:
        if not row.get("bol_awb"):
            continue
        ref = normal_reference(row["bol_awb"])
        if want:
            if ref == want:
                return row, looked
            continue
        if not status_ok(row.get("status")):
            looked.append(dict(row, reason="status '{0}' is not exactly '{1}'".format(
                row.get("status"), REQUIRED_STATUS)))
        elif ref in handled:
            looked.append(dict(row, reason="already processed"))
        else:
            return row, looked
        del looked[:-25]
    return None, looked


def ehub_rows(page):
    """Every row of the eHub source view, page by page, as the ETA run reads them."""
    import update_eta as A
    for number in range(1, A.MAX_TABLE_PAGES + 1):
        try:
            A.ensure_filtered_page(page, A.SOURCE_VIEW, number)
        except A.SkipShipment:
            return
        table = A.find_shipments_table(page)
        columns = A.build_header_map(table)
        columns.update(_extra_columns(table))
        rows = table.locator("tbody tr")
        for index in range(rows.count()):
            cells = rows.nth(index).locator("td")

            def cell(name):
                i = columns.get(name)
                if not isinstance(i, int) or cells.count() <= i:
                    return None
                return " ".join((cells.nth(i).inner_text() or "").split()) or None

            yield {"bol_awb": cell("bol_awb"), "carrier": cell("carrier"),
                   "status": cell("status"), "una_invoice": cell("una_invoice"),
                   "table_page": number, "view": A.SOURCE_VIEW}


def find_in_ehub(page, reference=None, skip=()):
    """
    Production. A named record is SEARCHED for, the way a person finds it:
    the Shipments List's own "BOL/AWB Number" box and Search. With no name,
    the ETA automation's own list reading (the BU view, filtered to Under
    Clearance) and the row rule.
    """
    if reference:
        rows, looked = search_rows(page, reference)
        row, _ = choose(rows, reference, skip)
        return row, looked
    return choose(ehub_rows(page), reference, skip)


def open_manage_in_ehub(page, row):
    import update_eta as A
    if row.get("view") == SEARCH_VIEW:
        click_manage_in_search(page, row["bol_awb"])
    else:
        A.click_manage_in_view(page, row.get("view") or A.SOURCE_VIEW, row["bol_awb"],
                               row.get("table_page") or 1)
    page.wait_for_timeout(800)
    to_documents(page)
    return {"url": page.url, "title": page.title()}


# ── THE SHIPMENTS LIST'S OWN SEARCH, AND MANAGE FROM ITS RESULT ──────────

SEARCH_VIEW = "SEARCH"
SEARCH_LABEL = r"^\s*BOL\s*/\s*AWB\s*(?:Number|No\.?)?\s*:?\s*$"
# The search box: the text input belonging to the "BOL/AWB Number" label
# (its `for`, its own container, or the next one after it). Marked so the
# fill lands on exactly that box.
SEARCH_BOX_JS = r"""(pattern) => {
  const re = new RegExp(pattern, 'i');
  const text = (el) => (el.innerText || '').trim();
  const labels = Array.from(document.querySelectorAll('label, span, div, p, b, strong, td'))
      .filter(el => re.test(text(el)) && !el.closest('table thead, th'));
  const inputs = () => Array.from(document.querySelectorAll(
      'input[type=text], input[type=search], input:not([type])'));
  for (const el of labels) {
    let target = el.htmlFor ? document.getElementById(el.htmlFor) : null;
    for (let up = 0, box = el.parentElement; !target && up < 2 && box; up++, box = box.parentElement) {
      const inside = Array.from(box.querySelectorAll('input[type=text], input[type=search], input:not([type])'));
      if (inside.length === 1) target = inside[0];
    }
    if (!target) target = inputs().find(i => el.compareDocumentPosition(i) & Node.DOCUMENT_POSITION_FOLLOWING);
    if (target) { target.setAttribute('data-ata-search', '1'); return true; }
  }
  return false;
}"""
MANAGE_LABEL = re.compile(r"^\s*manage\s*$", re.I)


def _wait(page, check, seconds, every_ms=400):
    end = time.time() + seconds
    while True:
        try:
            got = check()
        except Exception:
            got = None
        if got:
            return got
        if time.time() >= end:
            return None
        page.wait_for_timeout(every_ms)


def _extra_columns(table):
    """Columns the ETA run does not read but the PO job keeps as evidence."""
    try:
        heads = [" ".join((h or "").split()).casefold()
                 for h in table.locator("thead th").all_inner_texts()]
    except Exception:
        return {}
    return {"una_invoice": i for i, h in enumerate(heads) if re.search(r"una\+?\s*invoice", h)}


def _result_rows(page, reference=None):
    """The rows of the results table, read off its cells, or None when there is none."""
    import update_eta as A
    try:
        table = A.find_shipments_table(page)
        columns = A.build_header_map(table)
    except Exception:
        return None
    from .extract import normal_reference
    out = []
    extra = _extra_columns(table)
    rows = table.locator("tbody tr")
    for index in range(rows.count()):
        cells = rows.nth(index).locator("td")

        def cell(name):
            i = columns.get(name)
            if not isinstance(i, int) or cells.count() <= i:
                return None
            return " ".join((cells.nth(i).inner_text() or "").split()) or None

        row = {"bol_awb": cell("bol_awb"), "carrier": cell("carrier"), "status": cell("status"),
               "eta": cell("eta"), "table_page": 1, "view": SEARCH_VIEW}
        for name, i in extra.items():
            row[name] = " ".join((cells.nth(i).inner_text() or "").split()) or None \
                if cells.count() > i else None
        if row["bol_awb"] and (not reference or
                               normal_reference(row["bol_awb"]) == normal_reference(reference)):
            out.append(row)
    return out


def search_rows(page, reference):
    """
    The Shipments List, searched for one BOL/AWB — exactly what a person does:
    open the list, type the number in "BOL/AWB Number", press Search, read the
    row (its own Status cell). (rows, looked).
    """
    import update_eta as A
    A.invalidate_hub_state()
    page.goto(A.INTERNAL_URL, wait_until="domcontentloaded", timeout=60000)
    if not _wait(page, lambda: page.evaluate(SEARCH_BOX_JS, SEARCH_LABEL), 20):
        raise RuntimeError("the Shipments List has no 'BOL/AWB Number' search box")
    box = page.locator("[data-ata-search='1']").first
    box.fill("")
    box.fill(reference)
    button = A.first_visible([
        page.get_by_role("button", name="Search", exact=True),
        page.locator("input[type='submit'][value='Search' i], input[type='button'][value='Search' i]"),
        page.locator("button").filter(has_text=re.compile(r"^\s*search\s*$", re.I)),
        page.locator("a").filter(has_text=re.compile(r"^\s*search\s*$", re.I)),
    ], 5000)
    if button is None:
        raise RuntimeError("the Shipments List's Search button was not found")
    button.click(timeout=10000)
    rows = _wait(page, lambda: _result_rows(page, reference), 30)
    looked = [{"view": SEARCH_VIEW, "searched": reference,
               "result": "found" if rows else "no row for it in the search result"}]
    return rows or [], looked


def click_manage_in_search(page, reference):
    """Press Manage on THAT row of the search result (searching again if needed)."""
    import update_eta as A
    from .extract import normal_reference
    if not _result_rows(page, reference):
        search_rows(page, reference)
    table = A.find_shipments_table(page)
    columns = A.build_header_map(table)
    rows = table.locator("tbody tr")
    target = None
    for index in range(rows.count()):
        cells = rows.nth(index).locator("td")
        i = columns.get("bol_awb")
        if isinstance(i, int) and cells.count() > i and normal_reference(
                cells.nth(i).inner_text()) == normal_reference(reference):
            target = rows.nth(index)
            break
    if target is None:
        raise RuntimeError("{0} is not in the search result".format(reference))
    control = A.first_visible([
        target.locator("a, button").filter(has_text=MANAGE_LABEL),
        target.locator("input[type='submit'][value='Manage' i], input[type='button'][value='Manage' i]"),
    ], 5000)
    if control is None:
        raise RuntimeError("the row for {0} has no Manage button".format(reference))
    before = page.url
    control.click(timeout=10000)
    if not _wait(page, lambda: page.url != before or _manage_open(page), 30):
        raise RuntimeError("Manage was pressed for {0}, but the record did not open".format(
            reference))


def _manage_open(page):
    try:
        return page.get_by_text(re.compile(r"BU\s+Shipment\s+Info|^\s*Documents\s*$", re.I)) \
            .first.is_visible()
    except Exception:
        return False


def to_documents(page):
    """
    The Documents section is at the bottom of the record's BU Shipment Info
    (its general tab): select that tab when it is not the one showing, and
    scroll down. A tab and a scroll — nothing that changes the record.
    """
    try:
        tab = page.get_by_text(re.compile(r"^\s*BU\s+Shipment\s+Info\s*$", re.I)).first
        if tab.count() and tab.is_visible() and not NEVER_CLICK.search(tab.inner_text()):
            tab.click(timeout=5000)
            page.wait_for_timeout(600)
    except Exception:
        pass
    try:
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(400)
    except Exception:
        pass


# ── EVIDENCE: the page as it stood when a step failed ────────────────────

def capture(page, step):
    """A screenshot and the page's text, kept with the PO data; paths, or {}."""
    import os
    from pathlib import Path
    from . import store as S
    try:
        folder = Path(os.environ.get("PO_DATA_DIR") or S.DEFAULT_DIR) / "evidence"
        folder.mkdir(parents=True, exist_ok=True)
        stem = "{0}-{1}".format(datetime.now().strftime("%Y%m%d-%H%M%S-%f"),
                                re.sub(r"[^a-z0-9]+", "_", step.lower()))
        shot, text = folder / (stem + ".png"), folder / (stem + ".txt")
        page.screenshot(path=str(shot), full_page=True)
        text.write_text("URL: {0}\nTITLE: {1}\n\n{2}".format(
            page.url, page.title(), page.locator("body").inner_text(timeout=5000)),
            encoding="utf-8")
        return {"screenshot": str(shot), "page_text": str(text), "url": page.url}
    except Exception as error:
        return {"capture_error": str(error)[:120]}
