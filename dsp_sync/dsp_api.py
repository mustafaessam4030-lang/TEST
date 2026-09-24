"""Download the Manage Assets export from the DSP backend.

This is the same three-step flow the portal runs when you click the export
(download) icon on Manage Assets and choose CSV:

1. POST /assetdata/dealerFleet/export     -> {"exportID": ...}
2. GET  /assetdata/export/{exportID}      -> {"status": "Pending" | "Completed", "fileId": ...}
3. GET  /assetdata/download/{fileId}      -> the CSV file
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from pathlib import Path

import requests

from auth import DspSession, LoginRequired

log = logging.getLogger(__name__)


class DspClient:
    def __init__(self, session: DspSession, api_url: str, http: requests.Session | None = None):
        self.api_url = api_url.rstrip("/")
        self.dealer_code = session.dealer_code
        self.http = http or requests.Session()
        self.http.headers.update(
            {
                "Authorization": f"Bearer {session.token}",
                "x-dealer-code": session.dealer_code,
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://dsp.cat.com",
                "Referer": "https://dsp.cat.com/",
                "Cache-Control": "no-cache",
            }
        )

    def _call(self, method: str, path: str, **kw) -> requests.Response:
        resp = self.http.request(method, self.api_url + path, timeout=kw.pop("timeout", 120), **kw)
        if resp.status_code in (401, 403):
            raise LoginRequired(f"DSP rejected the token ({resp.status_code}) on {path}")
        resp.raise_for_status()
        return resp

    def export_assets(
        self,
        dest: Path,
        *,
        with_location: bool = True,
        poll_every_s: int = 30,
        timeout_s: int = 3600,
    ) -> Path:
        """Export every asset for the dealer to ``dest`` (a .csv path)."""
        payload = {
            "dealerCode": self.dealer_code,
            "filter": {},
            "operation": "exportCsv",
            "sortColumn": "serialNumber",
            "sortOrder": "asc",
            "exportSmhLocation": with_location,
            "isHomeSearch": False,
            "globalSearchInput": "",
        }
        export_id = self._call("POST", "/assetdata/dealerFleet/export", json=payload).json()["exportID"]
        log.info("Export requested (exportID=%s); waiting for DSP to build the file", export_id)

        deadline = time.monotonic() + timeout_s
        while True:
            status = self._call("GET", f"/assetdata/export/{export_id}").json()
            state = status.get("status")
            if state == "Completed":
                file_id = status["fileId"]
                break
            if state != "Pending":
                raise RuntimeError(f"DSP export {export_id} ended with status {state!r}: {status}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"DSP export {export_id} still pending after {timeout_s}s")
            time.sleep(poll_every_s)

        resp = self._call("GET", f"/assetdata/download/{file_id}", timeout=600)
        body = resp.content
        if body[:2] == b"PK":  # zipped export: take the CSV inside
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
                body = zf.read(name)

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(body)
        tmp.replace(dest)
        log.info("Saved %s (%.1f MB)", dest, len(body) / 1e6)
        return dest
