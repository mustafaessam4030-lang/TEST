#!/usr/bin/env python3
"""
Explain exactly what a run did and where it stopped.

Reads the run record from the gateway and the capture report from disk, and
answers the questions that matter after a failure: did it reach the sign-in
page, was the username field found, was it submitted, did the password step
appear, did MFA appear, did authentication succeed, and which selector could
not be proven.

Secrets are never read or printed; anything that looks like one is masked.

    python scripts/e2e/explain_run.py                     # the latest capture
    python scripts/e2e/explain_run.py --run-id run_01M3…  # a specific run
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = ROOT / "artifacts" / "capture"

# What each screenshot proves, in the order the capture takes them.
SHOT_MEANING = {
    "00-entry": "the sign-in page as it first appeared",
    "00-blocker": "a bot challenge or MFA prompt was detected at entry",
    "01-no-signin-form": "no sign-in field ever rendered",
    "02-blocker-after-username": "a challenge appeared after the username step",
    "02-no-password-field": "the password step never appeared",
    "02-after-submit": "the page immediately after the credentials were submitted",
    "02b-after-human-verification": "the page after a person completed verification",
    "auto-01-authenticated": "the first page AFTER a successful sign-in",
    "auto-02-detail": "the equipment detail page, AFTER its pane was scrolled\n                       to the equipment-details section",
    "recon-stopped": "the page where the run gave up",
}

SECRET_LIKE = re.compile(r"(password|passwd|secret|token|pwd)\s*[=:]\s*\S+", re.I)


def mask(text: Any) -> str:
    return SECRET_LIKE.sub(r"\1=***", str(text))


def fetch_run(run_id: str, api: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"{api}/v1/runs/{run_id}", timeout=5) as resp:
            return json.loads(resp.read()).get("run")
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def newest_capture() -> Path | None:
    if not CAPTURES.exists():
        return None
    runs = [d for d in CAPTURES.iterdir() if d.is_dir() and (d / "report.json").exists()]
    return max(runs, key=lambda d: d.stat().st_mtime) if runs else None


def answer(label: str, value: Any, detail: str = "") -> None:
    print(f"  {label:<34} {value}")
    if detail:
        print(f"  {'':<34} {detail}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id")
    ap.add_argument("--api", default="http://127.0.0.1:8080")
    ap.add_argument("--capture-dir", help="a specific artifacts/capture/<run> folder")
    args = ap.parse_args()

    print("\n" + "=" * 72)
    print("  RUN EXPLANATION")
    print("=" * 72)

    # ── the gateway's view ──────────────────────────────────────────────────
    if args.run_id:
        run = fetch_run(args.run_id, args.api)
        print(f"\nRun {args.run_id}")
        if run:
            print(f"  status         {run.get('status')}   error {run.get('error_code')}")
            print(f"  message        {mask(run.get('error_message'))}")
            print(f"  duration       {run.get('execution_time_ms')} ms")
            print("  steps:")
            for step in run.get("steps_executed") or []:
                url = (step.get("url") or "")[:70]
                print(f"    {step['seq']}. {step['step']:<16} {step['status']:<7} "
                      f"{step['duration_ms']:>6} ms  {url}")
        else:
            print("  (gateway not reachable — start it, or read the capture report below)")

    # ── the capture's view, which holds the authentication evidence ─────────
    folder = Path(args.capture_dir) if args.capture_dir else newest_capture()
    if not folder or not (folder / "report.json").exists():
        print("\nNo capture report found under artifacts/capture/.")
        return 1

    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    selectors = report.get("selectors") or {}
    shots = {p.stem for p in folder.glob("*.png")}
    stopped = report.get("stopped_reason") or ""
    steps = report.get("steps") or []
    step_names = {s.get("step") for s in steps}

    authenticated = "auto-01-authenticated" in shots
    saw_username = "login.username" in selectors or "auth:username" in step_names
    saw_password = "login.password" in selectors or "auth:password" in step_names
    mfa = bool(re.search(r"mfa|one[- ]time|verification|challenge|captcha", stopped, re.I)) \
        or "00-blocker" in shots or "02-blocker-after-username" in shots

    print(f"\nCapture  {folder.name}")
    print(f"  profile        {report.get('capture_profile')}")
    print(f"  base url       {report.get('base_url')}")
    print("\nEVIDENCE")
    answer("1. final URL", report.get("final_url") or
           (steps[-1].get("url") if steps and steps[-1].get("url") else
            next((s.get("url") for s in reversed(steps) if s.get("url")), "see report.json")))
    answer("2. page title", report.get("final_title", "(not recorded at this step)"))
    answer("3. reached the sign-in page", "YES" if "00-entry" in shots else "no evidence",
           "00-entry.png" if "00-entry" in shots else "")
    answer("4. username field detected", "YES" if saw_username else "NO",
           mask(selectors.get("login.username", {}).get("selector", "")) if saw_username else "")
    answer("5. username submitted", "YES" if "auth:username" in step_names or
           "login.username_submit" in selectors else "NO")
    answer("6. password field detected", "YES" if saw_password else "NO",
           mask(selectors.get("login.password", {}).get("selector", "")) if saw_password else "")
    answer("7. password submitted", "YES" if "auth:password" in step_names else "NO")
    answer("8. MFA / challenge appeared", "YES" if mfa else "no evidence")
    answer("9. authentication succeeded", "YES" if authenticated else "NO",
           "auto-01-authenticated.png exists — discovery only runs after sign-in"
           if authenticated else "the run never got past sign-in")

    # The detail page is read only after its own pane has been scrolled, so a
    # run that failed there fails for one of two very different reasons: the
    # scroll never revealed the section, or it did and the fields were not there.
    boot = report.get("app_boot") or {}
    if boot:
        answer("9a. the app finished rendering",
               "YES" if boot.get("booted") else "NO",
               f"{boot.get('waited_ms')} ms · title={str(boot.get('title') or '')[:40]!r} · "
               f"{boot.get('text')} chars · {boot.get('inputs')} input(s) · "
               f"{boot.get('landmarks')} landmark(s)")

    reveal = report.get("detail_reveal") or {}
    if reveal:
        pane = reveal.get("pane") or "(the window)"
        answer("9b. scrolled to the equipment details",
               "YES" if reveal.get("found") else "NO",
               f"pane {pane}, {reveal.get('steps')} step(s), "
               f"render settled={(reveal.get('settle') or {}).get('settled')}"
               if reveal.get("found") else
               f"the section never appeared after {reveal.get('steps')} scroll step(s)"
               f"{'; ' + mask(reveal['error']) if reveal.get('error') else ''}")
    labels = report.get("detail_labels") or {}
    if labels:
        answer("9c. labels the page printed",
               ", ".join(f"{k}={(v or {}).get('label')}" for k, v in labels.items()))
    groups = report.get("product_groups") or []
    if groups:
        answer("9d. 'Product - …' groups seen",
               f"{len(groups)}",
               "; ".join(f"{g.get('title')} ({g.get('row_count')} rows)" for g in groups[:5]))

    unresolved = report.get("unresolved") or []
    missing = report.get("missing_required") or []
    print("\n10. WHAT COULD NOT BE PROVEN")
    if stopped:
        print(f"  stopped because: {mask(stopped)}")
    if missing:
        print(f"  required, unresolved: {', '.join(missing)}")
    for name in unresolved:
        entry = selectors.get(name, {})
        why = "; ".join(entry.get("notes") or []) or "no reason recorded"
        print(f"    - {name:<26} {mask(why)}")
    if not (stopped or missing or unresolved):
        print("  nothing: every required target was proven")

    proven = {k: v for k, v in selectors.items() if v.get("selector")}
    if proven:
        print(f"\n  proven selectors ({len(proven)}):")
        for name, entry in proven.items():
            print(f"    + {name:<26} {entry.get('strategy','?'):<14} {entry['selector'][:52]}")

    print("\n11. SCREENSHOTS / DOM")
    for name in sorted(shots):
        print(f"  {name + '.png':<34} {SHOT_MEANING.get(name, '')}")
    for html in sorted(p.name for p in folder.glob("*.html")):
        print(f"  {html:<34} DOM snapshot (secrets redacted)")
    print(f"\n  folder: {folder}")
    print("=" * 72 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
