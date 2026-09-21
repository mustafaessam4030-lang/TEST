# 13 — Windows Deployment (the worker runs on your PC)

Claude's container builds and tests this project. **The automation worker runs on
your Windows machine**, because that is where SIS access, the credentials and —
if SIS asks for it — you, live.

```
Your Windows PC
┌──────────────────────────────────────────────────────────────┐
│  Maia chat (browser)  http://127.0.0.1:5173/maia.html        │
│        ↓ HTTP                                                 │
│  Tool gateway (FastAPI)  http://127.0.0.1:8080               │
│        ↓ in-process                                           │
│  SIS automation worker  →  Playwright  →  Chromium (visible)  │
└──────────────────────────────────────────┬───────────────────┘
                                           ↓ HTTPS
                                  https://sis2.cat.com
```

Credentials are read by the **worker process only**, from this machine's
environment. They are never written to the repo, never logged, never sent to the
model, and never leave your PC.

## The short version

1. Unzip the package.
2. Copy **login.example.txt** to **login.txt** and put your SIS username and
   password in it.
3. Double-click **START-MAIA.bat**.
4. The chat opens — ask Maia for a serial number.

`login.txt` stays on your PC: it is excluded from git and from the release
package, and only the automation worker reads it. Skip step 2 and the launcher
will ask you to type them instead, for that window only.

That is the whole thing. Everything below is detail for when you want it.

## 1. Required software

| What | Version | Why |
|---|---|---|
| Windows 10/11 | — | with normal SIS access from a browser |
| Python | 3.10–3.13 | the gateway and worker |

**On Python versions:** a just-released Python (3.14, 3.15…) has no published
builds of `pyyaml`, `pydantic-core` or `uvicorn`'s extras yet, so pip tries to
compile them and fails on any PC without a C++ toolchain. If the launcher finds
no version between 3.10 and 3.13 it installs 3.12 for your user account — no
admin rights — and carries on. Your newer Python is left exactly as it is.
| Chromium (via Playwright) | installed by the setup script | the automation browser |
| Git | any | to clone the repo |

Nothing else. No Docker, no Node, no database — the store defaults to in-memory
and you can point it at Snowflake later.

## 2. Environment variables

| Variable | Value | Set by you |
|---|---|---|
| `SIS_USERNAME` | your SIS username | yes |
| `SIS_PASSWORD` | your SIS password | yes |
| `MAIA_SIS_SECRET_REF` | `env://SIS` | the start script sets it |
| `MAIA_ALLOW_LIVE_AUTOMATION` | `true` | the start script sets it |
| `MAIA_HEADLESS` | `false` | the start script sets it (so you can watch) |

Session-only (safest — gone when you close the window):

```powershell
$env:SIS_USERNAME = "your.sis.username"
$secure = Read-Host "SIS password" -AsSecureString
$env:SIS_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
```

Persisted for your Windows user (stored by Windows, not by this project):

```powershell
setx SIS_USERNAME "your.sis.username"
setx SIS_PASSWORD "your-password"     # then open a NEW terminal
```

For a shared runner, use a vault instead and skip the two variables:
`$env:MAIA_SIS_SECRET_REF = "vault://kv/maia/cat_sis"` with `VAULT_ADDR` and
`VAULT_TOKEN` set.

## 3. Installation

```powershell
git clone <your-repo-url> maia
cd maia
.\scripts\windows\setup.ps1
```

That creates `.venv`, installs the Python packages, and installs Chromium.

If PowerShell refuses to run the scripts:
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

## 4. Playwright installation (what the setup script runs)

```powershell
python -m pip install -r services\automation\requirements-dev.txt
python -m playwright install chromium
```

## 5. Capture the SIS selectors — usually nothing to do

**The first search provisions this by itself.** If no contract exists, the
gateway runs the automatic capture as a step of that run — signing in, proving
each selector by using it, then continuing into your search. Maia shows it as
*"First run: learning the SIS page layout"*. You do not run a script.

Capture it ahead of time only if you would rather not have the first question
take a couple of minutes:

## 5b. Capturing ahead of time (optional)

```powershell
.\scripts\windows\doctor.ps1                                   # check readiness
.\scripts\windows\capture-sis.ps1 -Serial <a serial that EXISTS in SIS>
```

A visible Chromium opens and signs in with your environment credentials. Then it
discovers each selector and **proves it by using it**: the serial that exists must
produce a result, and `ZZZ00000` must produce the empty state. A control that
does not behave that way is not accepted.

* **MFA / human verification** → the script pauses and tells you to complete it
  in that window. Nothing about the verification is automated. Press Enter and it
  continues **in the same session**.
* Anything it cannot prove is written `TODO_CAPTURE` and the run stops. It never
  substitutes a guess.
* Prefer to point at the elements yourself? Add `-Manual`.

Result: `config\sis_selectors.json`, plus screenshots and a full report under
`artifacts\capture\`.

## 6. Start the SIS worker and Maia

One command starts the gateway, the worker and the chat UI together — the worker
is in-process with the gateway, so there is nothing else to launch:

```powershell
.\scripts\windows\start-maia.ps1
```

## 7. Open Maia

```
http://127.0.0.1:5173/maia.html
```

The script opens it for you. Click the chat bubble, bottom right.

## 8. Ask

```
Maia, find equipment data for serial number <your serial>
```

What you will see, in the chat, live:

```
• Checking internal data…
• Not in internal data — querying Caterpillar SIS…
✓ First run: learning the SIS page layout      (only the very first time)
• SIS automation running… Run run_01J…
✓ Starting a browser session
✓ Signing in to Caterpillar SIS
✓ Checking the SIS page contract
✓ Searching the serial number in SIS
✓ Reading the equipment record
✓ Normalizing · Validating · Saving
```

then the record, with **Source: Caterpillar SIS**, the retrieval timestamp and
the Automation Run ID. Ask again and it answers from the store in well under a
second, with no browser at all.

You never run a script per search. Maia triggers the worker through the gateway.

## If something fails

Maia shows the real error code and the run id, and no equipment values —
`LOGIN_FAILED`, `MFA_REQUIRED`, `CAPTCHA_DETECTED`, `WEBSITE_CHANGED`,
`SERIAL_NOT_FOUND`, `TIMEOUT`, `EXTRACTION_ERROR`, `INVALID_DATA`.

| Symptom | Cause | Fix |
|---|---|---|
| `LOGIN_FAILED` | credentials not in this session's environment, or rejected | re-set `SIS_USERNAME` / `SIS_PASSWORD` in the window you start from |
| `MFA_REQUIRED` | SIS wants verification and the browser is hidden | start without `-Headless` so you can complete it |
| `WEBSITE_CHANGED` | selectors missing or SIS changed its layout | re-run `capture-sis.ps1` |
| `SERIAL_NOT_FOUND` | SIS genuinely has no record | check the serial; it is cached as "not found" for 7 days |
| certificate errors | a TLS-inspecting proxy | add its CA to Chromium's Authorities list; never disable certificate checks |

Everything is in `artifacts\capture\` and `artifacts\` — screenshots, DOM
snapshots and the step-by-step run record. `GET http://127.0.0.1:8080/v1/runs/<id>`
returns the same audit trail as JSON.
