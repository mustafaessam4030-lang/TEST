"""API discovery: observe how the web application itself loads inspection data.

Opens a normal (headed by default) Chromium window. You log in as usual,
open the inspection page, run a search and page through results; the tool
records every fetch/XHR request the application makes and writes a report:

    discovery/<timestamp>/requests.json   every captured call (redacted)
    discovery/<timestamp>/REPORT.md       candidate endpoints + suggested source.yaml values
    discovery/<timestamp>/aria_snapshot.yaml  accessibility tree (roles/labels for Playwright)

Pass ``--probe`` with a value you can see on screen (an S/N or inspection
number); the report then shows exactly which endpoint and JSON path hold it.

Redaction: header *values* are never stored (names only); request/response
keys that look like credentials are masked; query values of sensitive
parameters are masked. Nothing is sent anywhere - the output stays local.

    python -m app.discovery --probe SYW57101 --probe 30081769
    python -m app.discovery --auto-login --navigate /inspections --duration 60 --headless
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from app.logger import REDACTED, configure_logging, redact

SENSITIVE_KEY = re.compile(
    r"(?i)(pass(word|wd)?|pwd|secret|token|auth|session|cookie|csrf|xsrf|api[-_]?key|credential|otp|mfa)"
)
MAX_STRING = 200
MAX_LIST_SAMPLE = 2


def scrub(value: Any, depth: int = 0) -> Any:
    """Redact sensitive keys, truncate long strings and lists."""
    if depth > 12:
        return "<max depth>"
    if isinstance(value, dict):
        return {k: (REDACTED if SENSITIVE_KEY.search(str(k)) else scrub(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, list):
        sample = [scrub(v, depth + 1) for v in value[:MAX_LIST_SAMPLE]]
        if len(value) > MAX_LIST_SAMPLE:
            sample.append(f"<... {len(value) - MAX_LIST_SAMPLE} more>")
        return sample
    if isinstance(value, str):
        text = redact(value)
        return text if len(text) <= MAX_STRING else text[:MAX_STRING] + "<...>"
    return value


def shape(value: Any, depth: int = 0) -> Any:
    """Type skeleton of a JSON document (keys and types, not values)."""
    if depth > 8:
        return "..."
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [f"list[{len(value)}]", shape(value[0], depth + 1)] if value else "list[0]"
    return type(value).__name__


def find_value_paths(value: Any, needle: str, prefix: str = "") -> list[str]:
    """Dotted paths whose scalar value equals ``needle`` (string comparison, trimmed)."""
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            found.extend(find_value_paths(v, needle, f"{prefix}{k}."))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found.extend(find_value_paths(v, needle, f"{prefix}{i}."))
    elif value is not None and str(value).strip() == needle:
        found.append(prefix.rstrip("."))
    return found


def list_candidates(value: Any, prefix: str = "", out: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every list of objects in the document: likely record collections."""
    out = [] if out is None else out
    if isinstance(value, dict):
        for k, v in value.items():
            list_candidates(v, f"{prefix}{k}.", out)
    elif isinstance(value, list):
        if value and all(isinstance(v, dict) for v in value):
            out.append({"records_path": prefix.rstrip("."), "length": len(value),
                        "keys": sorted({k for v in value for k in v})[:60]})
        for v in value[:1]:
            list_candidates(v, f"{prefix}0.", out)
    return out


def split_record_path(path: str) -> tuple[str, str] | None:
    """``data.items.3.sn`` -> (``data.items``, ``sn``): records_path and field path."""
    parts = path.split(".")
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].isdigit():
            return ".".join(parts[:i]), ".".join(parts[i + 1:])
    return None


def scrub_query(url: str) -> dict[str, str]:
    return {k: (REDACTED if SENSITIVE_KEY.search(k) else v) for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True)}


async def _capture(response: Any, probes: list[str], sink: list[dict[str, Any]]) -> None:
    request = response.request
    if request.resource_type not in ("fetch", "xhr"):
        return
    parts = urlsplit(request.url)
    request_headers = await request.all_headers()  # includes cookie/authorization; names only are kept
    response_headers = await response.all_headers()
    entry: dict[str, Any] = {
        "method": request.method,
        "origin": f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}",
        "path": parts.path,
        "query": scrub_query(request.url),
        "request_header_names": sorted(request_headers),
        "request_content_type": request_headers.get("content-type"),
        "status": response.status,
        "response_content_type": response_headers.get("content-type"),
        "response_header_names": sorted(response_headers),
    }
    body = request.post_data
    if body:
        try:
            parsed = json.loads(body)
            entry["request_json"] = scrub(parsed)
            if isinstance(parsed, dict) and "query" in parsed:
                entry["graphql_operation"] = parsed.get("operationName")
        except (json.JSONDecodeError, TypeError):
            entry["request_body"] = "<non-JSON body, not stored>"
    if "json" in (entry["response_content_type"] or ""):
        try:
            data = await response.json()
        except Exception as exc:  # noqa: BLE001
            entry["response_error"] = type(exc).__name__
        else:
            entry["response_shape"] = shape(data)
            entry["response_sample"] = scrub(data)
            entry["record_lists"] = list_candidates(data)
            entry["probe_matches"] = {p: find_value_paths(data, p) for p in probes}
    sink.append(entry)


def write_report(out_dir: Path, entries: list[dict[str, Any]], probes: list[str], aria: str | None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "requests.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    if aria:
        (out_dir / "aria_snapshot.yaml").write_text(aria, encoding="utf-8")

    def score(e: dict[str, Any]) -> tuple[int, int]:
        hits = sum(bool(v) for v in e.get("probe_matches", {}).values())
        biggest = max((c["length"] for c in e.get("record_lists", [])), default=0)
        return (hits, biggest)

    lines = ["# API discovery report", "",
             f"Captured {len(entries)} fetch/XHR responses. Probes: {', '.join(probes) or 'none'}.", "",
             "Only endpoints observed here may be configured in `config/source.yaml` (`api` section).", ""]
    for e in sorted(entries, key=score, reverse=True):
        if not e.get("record_lists") and not any(e.get("probe_matches", {}).values()):
            continue
        lines += [f"## {e['method']} {e['path']}  (HTTP {e['status']})", "",
                  f"- query params: `{json.dumps(e['query'])}`"]
        if "request_json" in e:
            lines.append(f"- JSON body: `{json.dumps(e['request_json'])[:500]}`")
        if e.get("graphql_operation"):
            lines.append(f"- GraphQL operation: `{e['graphql_operation']}`")
        for c in e.get("record_lists", []):
            lines.append(f"- record list at `records_path: \"{c['records_path']}\"` ({c['length']} items), keys: {', '.join(c['keys'])}")
        for probe, paths in e.get("probe_matches", {}).items():
            for p in paths:
                split = split_record_path(p)
                hint = f" -> records_path `{split[0]}`, field path `{split[1]}`" if split else ""
                lines.append(f"- probe `{probe}` found at `{p}`{hint}")
        lines.append("")
    lines += ["## Next steps", "",
              "1. Pick the endpoint that returns the inspection list; copy method/path/params into `api.request`.",
              "2. Set `api.records_path` and each `api.fields` path from the record keys above.",
              "3. Work out pagination by paging in the UI and comparing the captured query/body parameters.",
              "4. For the Playwright fallback use roles/labels/names from `aria_snapshot.yaml`.", ""]
    report = out_dir / "REPORT.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


async def discover(args: argparse.Namespace) -> Path:
    from playwright.async_api import async_playwright

    from app.config import Settings
    from app.diagnostics import Diagnostics

    settings = Settings()
    entries: list[dict[str, Any]] = []
    tasks: set[asyncio.Task[None]] = set()
    out_dir = args.out / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=args.headless)
        context = await browser.new_context()
        page = await context.new_page()

        def on_response(response: Any) -> None:
            task = asyncio.ensure_future(_capture(response, args.probe, entries))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        page.on("response", on_response)
        base = (args.base_url or settings.require_base_url()).rstrip("/")
        if args.auto_login:
            from app.auth.session import login_with_form
            from app.source_config import load_source_config

            login = load_source_config(settings.source_config_path).auth
            if login is None:
                raise SystemExit("--auto-login needs the 'auth' section in the source config")
            await login_with_form(page, settings, login, Diagnostics(settings.diagnostics_dir, "discovery"))
        await page.goto(base + (args.navigate or "/"))

        if args.duration:
            print(f"Recording for {args.duration}s ...", file=sys.stderr)
            await asyncio.sleep(args.duration)
        else:
            print("Log in, open the inspection page, search and page through results.\n"
                  "Press Enter here when done.", file=sys.stderr)
            await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        aria = None
        try:
            aria = await page.locator("body").aria_snapshot()
        except Exception:  # noqa: BLE001
            pass
        await browser.close()
    return write_report(out_dir, entries, args.probe, aria)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.discovery", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", help="Defaults to WEBSITE_BASE_URL")
    parser.add_argument("--navigate", help="Path to open first (default /)")
    parser.add_argument("--probe", action="append", default=[], help="A value visible in the UI to locate in responses")
    parser.add_argument("--auto-login", action="store_true", help="Log in with the configured login form")
    parser.add_argument("--duration", type=int, help="Record for N seconds instead of waiting for Enter")
    parser.add_argument("--headless", action="store_true", help="Run without a window (use with --auto-login)")
    parser.add_argument("--out", type=Path, default=Path("discovery"))
    args = parser.parse_args(argv)
    configure_logging()
    report = asyncio.run(discover(args))
    print(f"Discovery report written to {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
