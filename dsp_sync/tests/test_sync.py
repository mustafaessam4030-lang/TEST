"""Offline tests: a fake DSP backend, a fake Snowflake connection and a local
mock portal. Nothing here talks to dsp.cat.com or Snowflake."""

import http.server
import io
import json
import sys
import threading
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth  # noqa: E402
import snowflake_load  # noqa: E402
from auth import DspSession, LoginRequired  # noqa: E402
from dsp_api import DspClient  # noqa: E402

CSV = b"\xef\xbb\xbfSerial Number,Make,Customer Name,Customer ID\n11600929,CAT,STANBIC BANK,2969583833\n"


class FakeResp:
    def __init__(self, status=200, json_body=None, content=b""):
        self.status_code, self._json, self.content = status, json_body, content

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeHttp:
    def __init__(self, routes):
        self.headers, self.routes, self.calls = {}, routes, []

    def request(self, method, url, timeout=None, **kw):
        self.calls.append((method, url, kw))
        return self.routes[(method, url.split("prod-bff-dsp.cat.com")[1])].pop(0)


SESSION = DspSession(token="t" * 40, dealer_code="E180")


def test_export_polls_until_completed_and_saves_csv(tmp_path, monkeypatch):
    monkeypatch.setattr("dsp_api.time.sleep", lambda s: None)
    http = FakeHttp({
        ("POST", "/assetdata/dealerFleet/export"): [FakeResp(json_body={"exportID": "X1"})],
        ("GET", "/assetdata/export/X1"): [FakeResp(json_body={"status": "Pending"}),
                                           FakeResp(json_body={"status": "Completed", "fileId": "F9"})],
        ("GET", "/assetdata/download/F9"): [FakeResp(content=CSV)],
    })
    out = DspClient(SESSION, "https://prod-bff-dsp.cat.com", http).export_assets(tmp_path / "a.csv")
    assert out.read_bytes() == CSV
    assert http.headers["Authorization"] == "Bearer " + "t" * 40
    assert http.headers["x-dealer-code"] == "E180"
    body = http.calls[0][2]["json"]
    assert body["dealerCode"] == "E180" and body["operation"] == "exportCsv"


def test_export_unzips_zipped_download(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("assetPopulation.csv", CSV)
    http = FakeHttp({
        ("POST", "/assetdata/dealerFleet/export"): [FakeResp(json_body={"exportID": "X"})],
        ("GET", "/assetdata/export/X"): [FakeResp(json_body={"status": "Completed", "fileId": "F"})],
        ("GET", "/assetdata/download/F"): [FakeResp(content=buf.getvalue())],
    })
    out = DspClient(SESSION, "https://prod-bff-dsp.cat.com", http).export_assets(tmp_path / "a.csv")
    assert out.read_bytes() == CSV


def test_expired_token_raises_login_required(tmp_path):
    http = FakeHttp({("POST", "/assetdata/dealerFleet/export"): [FakeResp(status=401)]})
    with pytest.raises(LoginRequired):
        DspClient(SESSION, "https://prod-bff-dsp.cat.com", http).export_assets(tmp_path / "a.csv")


def test_normalize_columns():
    assert snowflake_load.normalize_columns(
        ["Serial Number", "Product | Plan", "Serial Number", "2nd Col", "", "Loaded At"]
    ) == ["SERIAL_NUMBER", "PRODUCT_PLAN", "SERIAL_NUMBER_2", "C_2ND_COL", "COLUMN", "SRC_LOADED_AT"]


class FakeCursor:
    def __init__(self, log, existing):
        self.log, self.existing = log, existing

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def fetchall(self):
        return [(c,) for c in self.existing]

    def close(self):
        pass


class FakeConn:
    def __init__(self, existing):
        self.sql, self.existing = [], existing

    def cursor(self):
        return FakeCursor(self.sql, self.existing)


def test_load_snapshot_replaces_day_and_adds_new_columns(tmp_path, monkeypatch):
    p = tmp_path / "a.csv"
    p.write_bytes(CSV)
    df = snowflake_load.read_export(p)
    staged = {}

    def fake_write_pandas(conn, frame, name, **kw):
        staged.update(name=name, rows=len(frame), **kw)
        return True, 1, len(frame), []

    monkeypatch.setattr(snowflake_load, "write_pandas", fake_write_pandas)
    conn = FakeConn(existing=["SERIAL_NUMBER", "MAKE", "SNAPSHOT_DATE", "LOADED_AT"])
    assert snowflake_load.load_snapshot(conn, df, "dsp_assets", date(2026, 9, 24)) == 1

    sql = [s for s, _ in conn.sql]
    assert any('ALTER TABLE "DSP_ASSETS" ADD COLUMN "CUSTOMER_NAME"' in s for s in sql)
    assert any('ALTER TABLE "DSP_ASSETS" ADD COLUMN "CUSTOMER_ID"' in s for s in sql)
    assert not any('ADD COLUMN "MAKE"' in s for s in sql)
    assert staged["name"] == "DSP_ASSETS_STAGE" and staged["table_type"] == "temporary"
    i = sql.index("BEGIN")
    assert sql[i + 1].startswith('DELETE FROM "DSP_ASSETS"') and sql[i + 2].startswith('INSERT INTO "DSP_ASSETS"')
    assert sql[i + 3] == "COMMIT"
    assert conn.sql[i + 1][1] == (date(2026, 9, 24),)


def test_load_refuses_empty_export():
    with pytest.raises(ValueError):
        snowflake_load.load_snapshot(FakeConn([]), pd.DataFrame(columns=["A"]), "T", date.today())


# --- auth against a local mock portal (needs Playwright's Chromium) ---------

PORTAL_HTML = b"""<script>
localStorage.setItem('access_token', 'stored-token-xxxxxxxxxxxxxxxxxxxxxxxx');
sessionStorage.setItem('dealer', JSON.stringify({dealerCode: 'E180'}));
fetch('/api/ping', {headers: {Authorization: 'Bearer live-token-xxxxxxxxxxxxxxxxxxxxxxxx', 'x-dealer-code': 'E180'}});
</script>"""


class PortalHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body, ctype = (b'{"ok":true}', "application/json") if self.path.startswith("/api") else (PORTAL_HTML, "text/html")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_get_session_captures_live_token(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PortalHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setattr(auth, "PORTAL_URL", base + "/")
    try:
        s = auth.get_session(tmp_path / "profile", base + "/api", timeout_s=20)
    finally:
        srv.shutdown()
    assert s == DspSession(token="live-token-" + "x" * 24, dealer_code="E180")
