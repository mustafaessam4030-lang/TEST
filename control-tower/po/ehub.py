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
    # Several entries, one identifier: the list order decides nothing. They
    # are all downloaded and compared byte for byte (EHubSource.fetch) — the
    # same file listed twice is one document; different files are a review.
    return None, candidates, ("{0} documents carry the same identifier {1}; they are compared "
                              "byte for byte before one is used".format(
                                  len(candidates), candidates[0]["identifier"])), "compare"


def _kind_of(error):
    text = str(error).lower()
    return "transient" if any(t in text for t in TRANSIENT) else "permanent"


# ── SIGN-IN AND IDENTITY ─────────────────────────────────────────────────

SIGN_IN_URL = re.compile(r"(log-?in|sign-?in|logon|oauth2|/adfs/)", re.I)


def auth_state(page):
    """
    'signed_in' / 'sign_in_required' / 'unknown'. A visible password field or
    a sign-in address means eHub is not signed in — the job stops (AUTH_REQUIRED)
    rather than reading a login page as if it were the list.
    """
    try:
        url = page.url or ""
    except Exception:
        return "unknown"
    try:
        password = page.evaluate("() => Array.from(document.querySelectorAll("
                                 "'input[type=password]')).some(e => e.offsetParent !== null)")
    except Exception:
        password = False
    if password or SIGN_IN_URL.search(urlparse(url).path or ""):
        return "sign_in_required"
    return "signed_in" if url and not url.startswith("about:") else "unknown"


MANAGE_IDENTITY_JS = r"""() => {
  const text = (document.body && document.body.innerText) || '';
  const valueAfter = (re) => {
    const labels = Array.from(document.querySelectorAll('label, span, td, th, div, b, strong'))
      .filter(el => re.test((el.innerText || '').trim()) && (el.innerText || '').length < 80);
    for (const el of labels) {
      const box = el.closest('div, td, tr') || el.parentElement;
      const input = (el.htmlFor && document.getElementById(el.htmlFor)) ||
        (box && box.querySelector('input, select, textarea')) ||
        (el.nextElementSibling && el.nextElementSibling.matches('input, select, textarea')
          ? el.nextElementSibling : null);
      if (input && (input.value || '').trim()) return input.value.trim();
      const next = el.nextElementSibling;
      if (next && (next.innerText || '').trim()) return next.innerText.trim().split('
')[0];
    }
    return null;
  };
  return {text: text.slice(0, 20000),
          status: valueAfter(/^\s*current\s+status\s*:?\s*$/i),
          declaration: valueAfter(/^\s*boe\s*\/\s*sgd.*declaration.*:?\s*$/i),
          shipment_ref: valueAfter(/^\s*(bol\s*\/\s*awb|bol|b\/l|awb|bill\s+of\s+lading|air\s*way\s*bill)(\s*(no\.?|number|#))?\s*:?\s*$/i)};
}"""


def manage_identity(page, row):
    """
    Whose record the Manage click opened, from the page itself: the row's
    BOL/AWB on the page; its current status; the declaration number it
    shows. {"reference_on_page", "status", "declaration", "url"}.
    """
    from .extract import normal_reference
    try:
        got = page.evaluate(MANAGE_IDENTITY_JS) or {}
    except Exception as error:
        return {"reference_on_page": None, "status": None, "declaration": None,
                "error": str(error)[:160]}
    ref = normal_reference(row.get("bol_awb"))
    flat = normal_reference(got.get("text"))
    shown = " ".join((got.get("shipment_ref") or "").split()) or None
    on_page = bool(ref) and ref in flat
    # A BOL/AWB the page labels as ITS shipment, that is not the row's — and
    # the row's appears nowhere on the page: Manage opened another record.
    conflict = shown if shown and ref and normal_reference(shown) != ref and not on_page \
        else None
    return {"reference_on_page": on_page, "shipment_ref_shown": shown,
            "identity_conflict": conflict,
            "status": " ".join((got.get("status") or "").split()) or None,
            "declaration": " ".join((got.get("declaration") or "").split()) or None,
            "url": getattr(page, "url", None)}


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
        body = response.body()
        ctype = (response.headers.get("content-type") or "").lower()
        if "text/html" in ctype:
            raise SourceError("eHub served an HTML page (content-type {0}), not the document — "
                              "a sign-in or error page".format(ctype[:60]), "unreadable")
        length = response.headers.get("content-length")
        if length and length.isdigit() and int(length) != len(body):
            raise SourceError("the download was cut short: {0} of {1} bytes".format(
                len(body), length), "transient")
        return body, url, "link", response.headers.get("content-disposition")
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

        def stop(message, kind, stage=None, **extra):
            error = SourceError(message, kind, extra.pop("candidates", None))
            error.stage = stage
            seal(False)
            error.trail = trail
            for k, v in extra.items():
                setattr(error, k, v)
            return error

        # 0. signed in? A sign-in page is never read as the list.
        if auth_state(self.page) == "sign_in_required":
            step("auth", False, evidence=capture(self.page, "auth"))
            raise stop("eHub is showing its sign-in page: the worker's browser is not signed "
                       "in. Nothing was read.", "auth", "auth")
        # 1. the eHub record
        try:
            row, looked = self.find_record(self.page, reference, self.skip)
        except SourceError as error:
            seal(False)
            error.trail = trail
            raise
        except Exception as error:
            if auth_state(self.page) == "sign_in_required":
                step("auth", False, evidence=capture(self.page, "auth"))
                raise stop("eHub sent the worker to its sign-in page while the Shipments list "
                           "was being read (the session ended). Nothing was read.", "auth",
                           "auth")
            step("ehub_record", False, error=str(error)[:200],
                 evidence=capture(self.page, "ehub_record"))
            raise stop("the eHub shipment list could not be read: {0}".format(str(error)[:200]),
                       _kind_of(error), "ehub_record")
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
            if reference:
                raise stop("{0} is not listed under eHub's 'Under Clearance' filter on view "
                           "{1} — its status is not Under Clearance, or it is not in eHub. "
                           "It was not opened.".format(reference, (looked[0] if looked else {})
                                                        .get("view", "BU")), "skipped",
                           "ehub_record", skip_reason="SKIPPED_NOT_UNDER_CLEARANCE")
            raise stop("no Under Clearance record is waiting in eHub (every listed one is "
                       "already processed or not Under Clearance)", "not_found", "ehub_record")
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
                       "skipped", "clearance_status", row=row,
                       skip_reason="SKIPPED_NOT_UNDER_CLEARANCE")

        # 3. Manage → the record's details
        try:
            opened = self.open_manage(self.page, row) or {}
        except Exception as error:
            step("manage", False, error=str(error)[:200], evidence=capture(self.page, "manage"))
            if auth_state(self.page) == "sign_in_required":
                raise stop("eHub sent the worker to its sign-in page when Manage was pressed "
                           "for {0}.".format(row.get("bol_awb")), "auth", "auth", row=row)
            raise stop("Manage could not be opened for {0}: {1}".format(
                row.get("bol_awb"), str(error)[:200]),
                "transient" if _kind_of(error) == "transient" else "navigation", "manage",
                row=row)
        try:
            opened.setdefault("url", self.page.url)
        except Exception:
            pass
        trail["manage"] = opened
        observed["manage"] = _host_of(opened.get("url"))
        trail["navigation_path"].append({"page": "Manage", "url": opened.get("url"),
                                         "row": row.get("bol_awb")})
        step("manage", True, url=opened.get("url"))

        # 3b. whose record is this? The page itself is read: the row's
        # BOL/AWB, its current status (it may have changed since the list
        # was read), and the declaration number it shows.
        identity = manage_identity(self.page, row)
        trail["identity"] = {k: identity.get(k) for k in ("reference_on_page", "status",
                                                          "declaration", "url",
                                                          "shipment_ref_shown",
                                                          "identity_conflict")}
        if identity.get("identity_conflict"):
            step("identity", False, shown=identity["identity_conflict"],
                 evidence=capture(self.page, "identity"))
            error = stop("Manage opened a record showing {0}, not {1} (whose BOL/AWB is nowhere "
                         "on the page): the page belongs to another shipment, nothing was "
                         "read from it.".format(identity["identity_conflict"],
                                                row.get("bol_awb")),
                         "identity_mismatch", "identity", row=row)
            error.identity = {"decision": "MISMATCH", "stage": "manage",
                              "contradictions": ["the Manage page shows {0}".format(
                                  identity["identity_conflict"])],
                              "why": "the Manage page shows {0}, not {1}".format(
                                  identity["identity_conflict"], row.get("bol_awb"))}
            raise error
        if identity.get("status") and not status_ok(identity["status"]):
            step("identity", False, page_status=identity["status"])
            raise stop("Manage for {0} shows its current status as '{1}', no longer '{2}': the "
                       "status changed after the list was read. Skipped; nothing was "
                       "downloaded.".format(row.get("bol_awb"), identity["status"],
                                            REQUIRED_STATUS), "skipped", "identity", row=row,
                       skip_reason="SKIPPED_STATUS_CHANGED")
        step("identity", True, reference_on_page=identity.get("reference_on_page"),
             page_status=identity.get("status"), declaration=identity.get("declaration"))

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
                       "nothing is guessed.".format(row.get("bol_awb")), "not_found",
                       "documents_section", row=row)

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
                           else ""), "no_bill_entry", "bill_entry", row=row)
        if outcome == "review":
            step("bill_entry", False, rule=rule)
            raise stop(rule[0].upper() + rule[1:] + ".", "review", "bill_entry", row=row,
                       candidates=[c["name"] for c in candidates])
        prefetched = None
        if outcome == "compare":
            import hashlib
            fetched = []
            for c in candidates[:4]:
                try:
                    got = download(self.page, section["frame"], c)
                except SourceError as error:
                    step("bill_entry", False, rule=rule, error=str(error)[:200])
                    seal(False)
                    error.trail = trail
                    error.stage = "download"
                    raise
                fetched.append((hashlib.sha256(got[0]).hexdigest(), c, got))
            hashes = sorted({h for h, _c, _g in fetched})
            trail["bill_entry"]["compared"] = [{"name": c["name"], "sha256": h}
                                               for h, c, _g in fetched]
            if len(hashes) != 1 or len(candidates) > 4:
                rule = ("{0} documents carry the identifier {1} but are different files "
                        "(sha256 {2}); there is no safe rule to choose — a person decides"
                        .format(len(candidates), candidates[0]["identifier"],
                                ", ".join(h[:12] for h in hashes)))
                trail["bill_entry"]["rule"] = rule
                step("bill_entry", False, rule=rule)
                raise stop(rule[0].upper() + rule[1:] + ".", "review", "bill_entry", row=row,
                           candidates=[c["name"] for c in candidates])
            selected = fetched[0][1]
            prefetched = fetched[0][2]
            rule = ("{0} documents carry the identifier {1} and are byte-identical (sha256 "
                    "{2}): one document listed {0} times".format(
                        len(candidates), selected["identifier"], hashes[0][:16]))
            trail["bill_entry"].update(selected=selected["name"],
                                       identifier=selected["identifier"], rule=rule)
        step("bill_entry", True, filename=selected["name"], rule=rule)
        step("identifier", True, identifier=selected["identifier"],
             source="the document name after 'Bill Entry'")

        # 6. download it
        try:
            data, url, method, disposition = prefetched[:4] if prefetched else \
                download(self.page, section["frame"], selected)[:4]
        except SourceError as error:
            step("download", False, error=str(error)[:200])
            seal(False)
            error.trail = trail
            error.stage = "download"
            raise
        if not data.startswith(b"%PDF"):
            step("download", False, error="not a PDF")
            raise stop("the Bill Entry document eHub served is not a PDF (an error page or "
                       "another file type was saved instead)", "unreadable", "download",
                       row=row)
        filename = selected["name"]
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition or "", re.I)
        served = unquote(m.group(1)) if m else None
        trail["download"] = {"method": method, "url": url, "served_filename": served,
                             "bytes": len(data), "link": selected.get("href") or None,
                             "element_id": selected.get("id") or None,
                             "content_type": getattr(self, "_last_content_type", None)}
        observed["download"] = _host_of(url)
        trail["navigation_path"].append({"page": "Bill Entry document", "url": url,
                                         "filename": filename})
        step("download", True, method=method, bytes=len(data), served_filename=served)
        seal(True)
        hub = dict(row, identifier=selected["identifier"], bill_entry=filename,
                   identity_on_manage=identity.get("reference_on_page"),
                   identity_conflict=identity.get("identity_conflict"),
                   manage_declaration=identity.get("declaration"))
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
    Production — the Shipments list, read the way an employee reads it: eHub →
    Shipments (Centralized Shipments Tracking, BU view, Status = Under
    Clearance) → the rows, page by page, each row's own Status cell. Nothing is
    typed into a search box and nothing has to be given: with no reference
    the first eligible row not yet processed is taken; a reference (a job
    re-run from the dashboard) is looked up in the same list.
    """
    return choose(ehub_rows(page), reference, skip)


def open_manage_in_ehub(page, row):
    click_manage_in_list(page, row)
    page.wait_for_timeout(800)
    to_documents(page)
    return {"url": page.url, "title": page.title()}


def click_manage_in_list(page, row):
    """
    Back to the Shipments list where the row was read (its view and page,
    then the others), and Manage on THAT row — the row's own control, matched
    by its BOL/AWB. Only a click: nothing on the list or the record changes.
    """
    import update_eta as A
    ref = row["bol_awb"]
    view = row.get("view") or A.SOURCE_VIEW
    first = row.get("table_page") or 1
    for number in [first] + [n for n in range(1, A.MAX_TABLE_PAGES + 1) if n != first]:
        try:
            A.ensure_filtered_page(page, view, number)
        except A.SkipShipment:
            continue
        target = A.find_row_by_bol(page, ref)
        if target is None:
            continue
        control = A.first_visible([
            target.locator("a, button").filter(has_text=MANAGE_LABEL),
            target.locator("input[type='submit'][value='Manage' i], "
                           "input[type='button'][value='Manage' i]"),
        ], 5000)
        if control is None:
            raise RuntimeError("the row for {0} has no Manage button".format(ref))
        before = page.url
        A.click_postback(control, "Manage for {0}".format(ref))
        A.invalidate_hub_state()
        if not _wait(page, lambda: page.url != before or _manage_open(page), 30):
            raise RuntimeError("Manage was pressed for {0}, but the record did not open".format(ref))
        return
    raise RuntimeError("{0} is no longer in eHub's Under Clearance list".format(ref))


# ── THE LIST'S MANAGE CONTROL, AND THE RECORD PAGE ─────────────────────

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


def _manage_open(page):
    try:
        return page.get_by_text(re.compile(r"BU\s+Shipment\s+Info|^\s*General\s*$|^\s*Documents\s*$",
                                           re.I)) \
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
        tab = page.get_by_text(re.compile(r"^\s*(?:BU\s+Shipment\s+Info|General)\s*$", re.I)).first
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
    # Never a picture of a sign-in page: a typed user name or password must
    # not end up in evidence. The page's address is kept instead.
    try:
        if page.evaluate("() => Array.from(document.querySelectorAll('input[type=password]'))"
                         ".some(e => e.offsetParent !== null)"):
            return {"not_captured": "a sign-in page (password field) — no screenshot or text "
                                    "is kept", "url": _safe_url(page.url)}
    except Exception:
        pass
    try:
        folder = Path(os.environ.get("PO_DATA_DIR") or S.DEFAULT_DIR) / "evidence"
        folder.mkdir(parents=True, exist_ok=True)
        stem = "{0}-{1}".format(datetime.now().strftime("%Y%m%d-%H%M%S-%f"),
                                re.sub(r"[^a-z0-9]+", "_", step.lower()))
        shot, text = folder / (stem + ".png"), folder / (stem + ".txt")
        page.screenshot(path=str(shot), full_page=True)
        text.write_text("URL: {0}\nTITLE: {1}\n\n{2}".format(
            _safe_url(page.url), page.title(), page.locator("body").inner_text(timeout=5000)),
            encoding="utf-8")
        return {"screenshot": str(shot), "page_text": str(text), "url": _safe_url(page.url)}
    except Exception as error:
        return {"capture_error": str(error)[:120]}


def _safe_url(url):
    """An address as evidence: no query string (it can carry session values)."""
    return re.sub(r"[?#].*$", "", str(url or ""))
