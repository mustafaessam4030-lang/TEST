"""
Find and retrieve the document attached to a Hub shipment.

    HubDocumentSource(page, open_shipment).fetch(reference)
        -> {"data": bytes, "filename", "url", "origin": "hub",
            "hub": {"bol_awb", "carrier", "status", "table_page"}}

`open_shipment(page, reference)` lands the browser on the shipment's page in
the Hub and returns the Hub's OWN values for that shipment (its BOL/AWB as
the Hub grid prints it) — the values the document is validated against. The
production one, `open_in_hub`, reuses the automation's existing Hub
navigation (update_eta.ensure_filtered_page / collect_supported_shipments /
click_manage_in_view); nothing about the Hub is reimplemented here.

The document is found on that page by its link: an <a> or button whose
address ends in .pdf or whose text names a document (DOC_LINK_TEXT —
"Bill of Entry", "BOE", "Declaration", "Customs" — or PO_DOC_LINK_TEXT to
override). One match is downloaded; none is DOCUMENT NOT FOUND; several
different ones is AMBIGUOUS and nothing is chosen — the operator names it
(request field `document_name`) and the job is run again.

The download goes through the same browser context, so it carries the
Hub's own sign-in. The bytes must start with %PDF.

Check this against the real Hub with:   python -m po hub-links <BOL/AWB>
"""

import os
import re
from urllib.parse import unquote, urljoin, urlparse

from .pipeline import SourceError

DOC_LINK_TEXT = os.environ.get("PO_DOC_LINK_TEXT") or \
    r"bill\s*of\s*entry|\bboe\b|declaration|customs|duty|assessment"
TRANSIENT = ("timeout", "net::", "err_", "connection", "econnreset", "502", "503", "504")


def _kind_of(error):
    text = str(error).lower()
    return "transient" if any(t in text for t in TRANSIENT) else "permanent"


def links_on(page):
    """Every link or button on the page (and its frames) that could be a document."""
    out = []
    for frame in page.frames:
        try:
            rows = frame.evaluate("""() => Array.from(document.querySelectorAll(
                'a[href], button, input[type=button], input[type=submit]')).map(e => ({
                  text: (e.innerText || e.value || e.title || '').trim().slice(0, 160),
                  href: e.getAttribute('href') || '',
                  download: e.getAttribute('download') || ''}))""")
        except Exception:
            continue
        for row in rows:
            row["frame_url"] = frame.url
            out.append(row)
    return out


def candidates(links, wanted=None):
    """The document links, best first. `wanted` (a name the operator gave) narrows them."""
    pattern = re.compile(DOC_LINK_TEXT, re.I)
    found = []
    for link in links:
        href = link.get("href") or ""
        path = unquote(urlparse(href).path).lower()
        is_pdf = path.endswith(".pdf") or link.get("download", "").lower().endswith(".pdf")
        named = bool(pattern.search(link.get("text") or "")) or bool(pattern.search(path))
        if not (is_pdf or (named and href and not href.startswith(("javascript:", "#")))):
            continue
        if wanted and wanted.lower() not in ((link.get("text") or "") + " " + path).lower():
            continue
        found.append(dict(link, score=(2 if is_pdf else 0) + (1 if named else 0)))
    found.sort(key=lambda l: -l["score"])
    best = [l for l in found if l["score"] == (found[0]["score"] if found else 0)]
    distinct = {urljoin(l["frame_url"], l["href"]) for l in best}
    return found, best, distinct


class HubDocumentSource(object):
    def __init__(self, page, open_shipment, wanted=None):
        self.page = page
        self.open_shipment = open_shipment
        self.wanted = wanted

    def fetch(self, reference):
        try:
            hub = self.open_shipment(self.page, reference)
        except SourceError:
            raise
        except Exception as error:
            raise SourceError("the Hub shipment page for {0} could not be opened: {1}".format(
                reference, str(error)[:200]), _kind_of(error))
        if not hub:
            raise SourceError("{0} is not in the Hub's shipment list".format(reference),
                              "not_found")
        found, best, distinct = candidates(links_on(self.page), self.wanted)
        if not found:
            raise SourceError("no document is attached to {0} on its Hub page{1}".format(
                reference, " matching '{0}'".format(self.wanted) if self.wanted else ""),
                "not_found")
        if len(distinct) > 1:
            raise SourceError("{0} documents on the Hub page could be the one; none is "
                              "chosen. Name it and run again.".format(len(distinct)),
                              "ambiguous", [l.get("text") or l.get("href") for l in best])
        link = best[0]
        url = urljoin(link["frame_url"], link["href"])
        try:
            response = self.page.context.request.get(url, timeout=60000)
        except Exception as error:
            raise SourceError("the document could not be downloaded: {0}".format(
                str(error)[:200]), _kind_of(error))
        if response.status >= 500:
            raise SourceError("the Hub answered {0} for the document".format(response.status),
                              "transient")
        if response.status >= 400:
            raise SourceError("the Hub answered {0} for the document".format(response.status),
                              "not_found" if response.status == 404 else "permanent")
        data = response.body()
        if not data.startswith(b"%PDF"):
            raise SourceError("the linked document is not a PDF ({0})".format(
                response.headers.get("content-type", "unknown type")), "permanent")
        filename = (unquote(urlparse(url).path).rsplit("/", 1)[-1] or "document.pdf")
        disposition = response.headers.get("content-disposition") or ""
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition, re.I)
        if m:
            filename = unquote(m.group(1))
        return {"data": data, "filename": filename, "url": url, "origin": "hub", "hub": hub,
                "link_text": link.get("text")}


def open_in_hub(page, reference):
    """
    Production: the existing Hub navigation. Finds the row carrying this
    reference on the source view (any status, any carrier), reads the Hub's
    own BOL/AWB and carrier off it, then opens its Manage page. Returns those
    Hub values, or None when no page of the view has the row.
    """
    import update_eta as A
    for number in range(1, A.MAX_TABLE_PAGES + 1):
        try:
            A.ensure_filtered_page(page, A.SOURCE_VIEW, number)
        except A.SkipShipment:
            break
        row = A.find_row_by_bol(page, reference)
        if row is None:
            continue
        columns = A.build_header_map(A.find_shipments_table(page))
        cells = row.locator("td")

        def cell(name):
            index = columns.get(name)
            if not isinstance(index, int) or cells.count() <= index:
                return None
            return " ".join((cells.nth(index).inner_text() or "").split()) or None

        hub = {"bol_awb": cell("bol_awb"), "carrier": cell("carrier"),
               "status": cell("status"), "table_page": number, "view": A.SOURCE_VIEW}
        A.click_manage_in_view(page, A.SOURCE_VIEW, hub["bol_awb"] or reference, number)
        return hub
    return None
