# ATA Control Tower — Remote Platform

The Control Tower as a multi-user web application: a stable address, each
employee signing in with their own work account, roles enforced on the
server, an audit trail, runs started from the web and carried out by the
**existing** automation on a Windows worker, and Human Actions completed in
the operator's own browser.

Nothing in the automation was rewritten. The worker runs `update_eta.py`
through the same `Supervisor` the local Control Tower already used. The
runner gained four opt-in hooks, all inert unless the worker agent started
it: it takes its run id from the control plane (`CT_RUN_ID`), honours a dry
run (`CT_DRY_RUN`), opens a loopback-only session endpoint
(`CT_SESSION_PORT`), and lets a remote viewer see its paused tab while
`wait_for_human()` waits.

---

## 1. Architecture

```
 Operator's browser (laptop, desktop, tablet, phone)
        │  HTTPS, session cookie, CSRF token
        ▼
 https://ata.mantrac.com  ── Azure App Service (Linux, Python 3.11) ─────────┐
 │  controlplane/app.py                                                      │
 │   ├─ authentication (local accounts / Microsoft Entra ID)                 │
 │   ├─ authorisation (rbac.py, every route, server-side)                    │
 │   ├─ dashboard page + API + SSE stream (dashboard/static, reused)         │
 │   ├─ ATLAS copilot (dashboard/assistant.py, on the authoritative state)   │
 │   ├─ run orchestration, worker commands, reconciliation (runs.py)         │
 │   ├─ Human Action claims + live-view relay (relay.py, memory only)        │
 │   └─ audit trail (audit.py)                                               │
 │          │                                                                │
 │          ├── PostgreSQL Flexible Server: users, sessions, audit, runs,    │
 │          │   run state, commands, claims                                  │
 │          ├── Key Vault (database URL, Entra secret) via managed identity  │
 │          ├── /home/ata/intelligence: ATLAS learning + evidence            │
 │          └── Application Insights / Log Analytics                         │
 └──────────────▲────────────────────────────────────────────────────────────┘
                │  OUTBOUND HTTPS only, bearer token per worker
                │  (heartbeat · long-poll commands · state · frames/input · learning)
 Windows worker VM (no public IP, no inbound rule) ──────────────────────────┐
 │  worker/agent.py ── dashboard/supervisor.py ── update_eta.py (unchanged    │
 │                                                 automation, same run id)   │
 │      ▲ loopback 127.0.0.1, per-run token          │                        │
 │      └──────────── remote_session.py ◄────────────┘ Playwright ── Edge     │
 │                    (CDP screencast + input, only inside wait_for_human)   │
 └──────────────────────────────────────────────────────────── carriers, Hub ┘
```

| Layer | Is | Never |
|---|---|---|
| User's browser | remote control, observability, the human interaction surface | the automation, or a source of truth |
| Control plane | source of truth for people, roles, runs, claims, audit | a browser driver; it never touches the Hub |
| Windows worker | the execution environment: Edge, Playwright, `update_eta.py` | reachable from the internet |
| ATLAS | intelligence over the authoritative state and verified records | an actor: it reads, explains and proposes |
| Learning store | verified operational memory, labelled production or test | trained from security codes, CAPTCHAs or credentials |

---

## 2. Files changed

**New**

| Path | What |
|---|---|
| `controlplane/__init__.py`, `__main__.py` | package; CLI: `serve`, `create-admin`, `add-worker`, `workers`, `users` |
| `controlplane/config.py` | settings from the environment |
| `controlplane/db.py` | SQLite / PostgreSQL schema and pool, transactions |
| `controlplane/security.py` | scrypt hashing, tokens, rate limiter, CSP and security headers |
| `controlplane/rbac.py` | roles, permissions, the matrix |
| `controlplane/users.py` | accounts, invites, sessions, role/active changes, preferences |
| `controlplane/oidc.py` | Microsoft Entra ID (OIDC code + PKCE, RS256 verification) |
| `controlplane/audit.py` | append-only audit trail with secret scrubbing |
| `controlplane/runs.py` | runs, workers, commands, heartbeat, disconnect/reconcile |
| `controlplane/relay.py` | Human Action claims and the in-memory live-view relay |
| `controlplane/app.py` | the HTTP application and worker API |
| `controlplane/static/login.html`, `static/login/login.css`, `login.js` | sign-in and set-password page |
| `worker/__init__.py`, `__main__.py`, `agent.py` | the Windows worker agent |
| `remote_session.py` | runner-side screencast/input pump and loopback endpoint |
| `deploy/azure/main.bicep`, `main.parameters.example.json` | Azure resources |
| `deploy/azure/package_controlplane.py` | builds the App Service zip |
| `deploy/azure/install_worker.ps1` | sets up the worker VM |
| `fixtures/remote_runner.py`, `carrier_stub.py`, `platform_client.py` | **test fixtures only** |
| `test_platform.py`, `test_remote_session.py` | the new suites |
| `PLATFORM.md` | this document |

**Changed**

| Path | Change |
|---|---|
| `update_eta.py` | `CT_RUN_ID` (validated) replaces the generated run id when the worker supplies one; `CT_DRY_RUN=1` can only turn dry-run on; optional `remote_session` import; loopback session endpoint started when `CT_SESSION_PORT` is set; `wait_for_human()` and the post-verification check wait through `_human_pause()` (identical to `page.wait_for_timeout` unless a remote viewer is attached); `ATLAS_DATA_ORIGIN=production` set in `main()` |
| `dashboard/supervisor.py` | `start(extra_env=None)` passes `CT_*` variables to the run; `ATA_RUNNER_SCRIPT` / `ATA_RUNTIME_DIR` (defaults unchanged); production data origin |
| `dashboard/static/index.html` | theme system (Light/Dark/System, designed dark tokens, no-flash boot); user chip, role, theme switch, sign out; health strip; worker-disconnected notice; Access page (users, audit, workers); remote Human Action viewer; proposal Approve/Reject for admins; learning data-origin badge; CSRF on requests; inline event handlers moved to script (strict CSP) |
| `dashboard/server.py` | learning endpoint reports `data_origin` |
| `intelligence/store.py` | per-store `ORIGIN` marker; a process of the other origin is refused |
| `intelligence/evidence.py` | `register_synced()` for captures forwarded by the worker (hash-checked) |
| `run_tests.py` | suites run with `ATLAS_DATA_ORIGIN=test`; the two new suites |
| `.gitignore` | `controlplane/data/`, `dist/` |

---

## 3. Authentication

* **Every person has their own account.** There is no shared password.
* **Local accounts**: an admin creates the account; the system issues a
  one-time link (72 h, single use, token hash stored) and the person sets
  their own password. Passwords: scrypt (N=2^15, r=8, p=1, 16-byte salt),
  at least 12 characters mixing three character classes.
* **Microsoft Entra ID** (preferred for production): OpenID Connect
  authorization-code flow with PKCE (S256), confidential client. The ID token
  is verified before anyone is signed in — RS256 signature against the
  tenant's published keys (fetched over TLS, cached, refreshed on unknown
  `kid`), issuer, audience, tenant id, expiry, not-before, and the nonce
  generated for that sign-in. The state is single-use and lives 10 minutes.
  Domains are restricted with `ENTRA_ALLOWED_DOMAINS`. A person with no ATA
  account is refused unless `ENTRA_AUTO_PROVISION=1` (then created as Viewer).
  With `ENTRA_ROLE_CLAIMS=1` the role comes from app roles `ATA.Admin`,
  `ATA.Operator`, `ATA.Viewer` at every sign-in.
* **Sessions** are server-side: a 256-bit random token in the
  `__Host-ata_session` cookie (HttpOnly, Secure, SameSite=Lax, Path=/); the
  database holds only its SHA-256. A session ends at sign-out, after
  `ATA_SESSION_IDLE_MIN` (30) minutes idle, after `ATA_SESSION_MAX_HOURS`
  (12) hours in all, and immediately on deactivation, role change or
  "Reset access". An expired session is reported as such (`401
  session_expired`) and audited.
* **Lockout**: 5 wrong passwords lock the account for 15 minutes; sign-in is
  also rate-limited per address and per email. The page says only "That email
  or password is not right" whatever the reason.
* No secret is ever sent to the browser: no password, hash, token hash,
  client secret or worker token appears in any API response (the one-time
  link and a new worker token are shown once, to the admin who created them).

## 4. Roles and permissions (RBAC matrix)

| Permission | Meaning | Admin | Operator | Viewer |
|---|---|:-:|:-:|:-:|
| `dashboard.view` | see the Control Tower, runs, shipments, ATLAS insights | ✓ | ✓ | ✓ |
| `runs.view` | every run and its history, CSV export | ✓ | ✓ | ✓ |
| `health.view` | worker heartbeat and system health | ✓ | ✓ | ✓ |
| `atlas.chat` | ask ATLAS | ✓ | ✓ | ✓ |
| `runs.start` | start the automation | ✓ | ✓ | |
| `runs.stop` | stop / pause a run | ✓ | ✓ | |
| `human.act` | Open & Continue, use the live browser view | ✓ | ✓ | |
| `evidence.view` | open screenshots and evidence | ✓ | ✓ | |
| `evidence.upload` | attach a screenshot in chat | ✓ | ✓ | |
| `atlas.approve` | approve / reject ATLAS proposals | ✓ | | |
| `users.manage` | create users, roles, activate, reset | ✓ | | |
| `audit.view` | read the audit log | ✓ | | |
| `settings.manage` | system settings, register workers | ✓ | | |

Enforced in `controlplane/app.py` on every request; the page draws controls
from the same list only as a courtesy. Unauthenticated → **401**; signed in
without the permission → **403** (audited as `ACCESS_DENIED`). There is no
default allow: an unlisted route is 404. Self-protection: an admin cannot
deactivate themself or change their own role, and the last active admin
cannot be removed or demoted.

## 5. API endpoints

Public: `GET /healthz` · `GET /login` · `GET /set-password` ·
`GET /api/auth/config` · `POST /api/auth/login` · `POST /api/auth/set-password` ·
`GET /auth/sso/start` · `GET /auth/sso/callback` · `GET /static/brand/*`, `/static/login/*`

Signed in (permission in brackets; every non-GET needs `X-CSRF-Token` and a same-site `Origin`):

| Method & path | Permission | Notes |
|---|---|---|
| `GET /` , `/intro`, `/static/*` | session | dashboard page (CSP-hashed inline scripts) |
| `GET /api/auth/me` | session | person, permissions, CSRF token |
| `POST /api/auth/logout` | session | |
| `POST /api/me/prefs` | session | `{"theme": "light"\|"dark"\|"system"}` |
| `GET /api/state` | dashboard.view | authoritative state + `platform` block |
| `GET /api/stream` | dashboard.view | SSE `state` events; `auth` event when signed out |
| `GET /api/health` | health.view | API, worker, browser, ATLAS, storage, current run |
| `GET /api/runs`, `GET /api/runs/{id}` | runs.view | |
| `POST /api/runs` | runs.start | `{dry_run?, max_records?, max_pages?}` → 200 / 409 run active / 503 worker unavailable |
| `POST /api/runs/{id}/stop\|pause\|resume` | runs.stop | `{force?}` |
| `POST /api/control` | runs.start / runs.stop | the dashboard's existing buttons |
| `POST /api/human` | human.act | `{op:"open"\|"resume", run_id, action_id}` → claim + worker command |
| `GET /api/session/{action}/status` | human.act | holder, view status |
| `GET /api/session/{action}/frame?after=N` | human.act + holder | long-poll JPEG, `X-Seq`, `X-Meta`; renews the claim |
| `POST /api/session/{action}/input` | human.act + holder | `{events:[…]}` pointer/key events |
| `POST /api/session/{action}/release` | human.act + holder | |
| `POST /api/ask`, `POST /api/feedback` | atlas.chat | |
| `GET /api/atlas`, `/api/atlas/learning`, `/maturity`, `/plan`, `/proposals`, `/api/ml` | dashboard.view | |
| `POST /api/atlas/proposals/{id}/approve\|reject` | atlas.approve | recorded only, never deployed |
| `GET /api/evidence`, `/api/evidence/file` | evidence.view | viewing is audited |
| `POST /api/evidence/upload` | evidence.upload | |
| `GET /api/export.csv` | runs.view | |
| `GET/POST /api/admin/users`, `PATCH /api/admin/users/{id}`, `POST …/reset`, `POST …/revoke` | users.manage | |
| `GET /api/admin/audit` | audit.view | filters: action, user, run, before |
| `GET/POST /api/admin/workers` | settings.manage | POST returns the token once |
| `GET /api/admin/rbac` | users.manage | the matrix |

## 6. Worker protocol

All calls are made **by the worker** to `https://…/worker/v1/*` with
`Authorization: Bearer w_<id>.<secret>` (only the SHA-256 is stored; an admin
issues it with `python -m controlplane add-worker` or Access → Workers).

| Call | Purpose |
|---|---|
| `POST heartbeat` | every 10 s: state (IDLE/BUSY/DEGRADED), version, current run, runner pid, current step, browser state, Edge found, problems, last run (id, exit code) |
| `GET commands?wait=20` | long-poll; commands are marked delivered, re-offered if unanswered for 20 s (max 3), expired after 120 s |
| `POST commands/{id}/result` | `{ok, message}` |
| `POST runs/{id}/state` | the run's published state (the same JSON the supervisor reads), sent when it changes |
| `POST runs/{id}/ended` | `{exit_code, stopped}` |
| `POST session/{action}/frame` | one JPEG frame (`X-Meta`: page width/height) |
| `GET session/{action}/input?wait=8` | the holder's pointer/key events |
| `POST session/{action}/status` | whether the view is attached/streaming, and why not |
| `POST intel/events`, `POST intel/evidence` | ATLAS records and captures, with the store's origin |

Commands: `start_run`, `stop_run`, `pause_run`, `resume_run`, `reprocess`,
`human`, `session_attach`, `session_detach`. A worker can act only on the run
it carries and on sessions claimed for that run.

**States.** Worker: `ONLINE`/`IDLE`/`BUSY`/`DEGRADED` while heartbeats
arrive; `OFFLINE` after `ATA_WORKER_OFFLINE_S` (30 s) of silence. Run:
`QUEUED → STARTING → RUNNING → COMPLETED | STOPPED | FAILED`,
`FAILED_TO_START`, `WORKER_DISCONNECTED` (worker silent; nothing is
assumed), `INTERRUPTED`. On reconnect the run is reconciled only from the
worker's own report: still running → `RUNNING`; ended with an exit code →
`COMPLETED`/`FAILED`; exit code lost → the run's last own report decides
(`finished` → `COMPLETED`, otherwise `INTERRUPTED`). A restarted agent adopts
a run whose process is still alive (by pid) instead of starting a second one.

## 7. Human Action — remote session

**Technology.** The Chrome DevTools Protocol, which Edge speaks:
`Page.startScreencast` (JPEG frames on paint), `Page.captureScreenshot` for
the first frame, and `Input.dispatchMouseEvent` / `Input.dispatchKeyEvent`
for the person's pointer and keys — opened with Playwright's
`context.new_cdp_session(page)` on **the run's own tab**. Nothing moves to the
user's browser except pictures of that tab.

**Flow.**
1. The run meets a verification, waits `HUMAN_QUEUE_GRACE_MS` (30 s) and parks
   the shipment (unchanged queue behaviour; the run carries on, holds up to
   10 minutes before ending).
2. An operator presses **Open & Continue**. The control plane checks the
   permission, the run, the action id, that the task is still waiting, and
   claims it for that one person (a lease renewed by every frame request;
   anyone else is told who holds it).
3. Two commands go to the worker: the existing scoped `open` request (through
   the supervisor's control file, validated again by the run) and
   `session_attach`. The agent tells the run's loopback endpoint which action
   may be shown.
4. At the next safe point the run brings that shipment back to the
   verification step (unchanged). While `wait_for_human()` waits, each poll
   interval is spent in the pump: frames out, the holder's events in, on the
   Playwright thread, for that tab only.
5. The person completes the verification in the browser view. The dashboard
   says: *Complete the verification in the browser below. ATA will continue
   automatically after the verification is confirmed.* No Resume is needed.
6. The run's own `ready()` check sees the result, reads it again after it
   settles (POST_VERIFICATION_CHECK), and continues: extraction → validation →
   Hub write → read-back. The view shows each step only as the run's state
   reports it: *Verification confirmed*, *Extracting*, *Validating*,
   *Hub write*, *Read-back*, *Verified success*.
7. The pump stops when the wait ends; the claim closes when the run reports
   the task's final state; the browser session follows the existing
   lifecycle. Outcomes: `SUCCESS`; `VERIFICATION_NOT_CONFIRMED`/`FAILED`
   (= NOT_SUCCESS_AFTER_HUMAN); `HUMAN_SESSION_LOST`; `TIMEOUT`. Nothing is
   written unless the run itself verified the page.

**What passes through, and what never stays.** Frames and keystrokes cross
the worker agent and the control plane **in memory**: never logged, never
written to disk or the database, never given to ATLAS, the evidence store or
OCR, and dropped when the claim ends. The keystrokes reach the carrier page
exactly as typed — that is what a remote view is — but no component reads,
records or interprets them; the relay checks only their shape. ATLAS has no
path to this data. Verified by test: the code typed through the view appears
in no file, table, log, audit entry or dashboard payload, even when the
carrier echoes it in a URL.

## 8. Security model

| Control | Where |
|---|---|
| HTTPS only; HSTS; TLS 1.2+; `__Host-` Secure cookie | App Service `httpsOnly`, `minTlsVersion`, app headers |
| Authentication | local scrypt accounts or Entra ID OIDC (PKCE, RS256) |
| Authorisation | `rbac.py`, checked per request on the server |
| CSRF | per-session token in `X-CSRF-Token` + `Origin`/`Sec-Fetch-Site` check on every state change |
| Content Security Policy | scripts only from this origin or the page's own SHA-256-hashed inline blocks; no eval; `frame-ancestors 'none'`; `connect-src 'self'` |
| Other headers | nosniff, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, COOP/CORP, Permissions-Policy, `Cache-Control: no-store` |
| Rate limits | 600 req/min per address; sign-in 20/10 min per address and 10/10 min per email; bad worker tokens 30/10 min |
| Secrets | Key Vault references via managed identity; environment only; never in responses |
| Worker | outbound-only; per-worker bearer token (hash stored); acts only on its own run; no inbound port, NSG denies internet inbound |
| Run-side endpoint | 127.0.0.1 only, per-run 256-bit token, exists only while the agent's run runs |
| Audit | append-only from the app; secret-looking metadata keys dropped |
| ATLAS boundary | from chat only Open & Continue (and only with `human.act`); re-runs are refused; proposals are recorded, never deployed |
| Verification | the automation never reads, solves, OCRs, types or stores codes; captures exclude verification screens (unchanged) |

## 9. Database / storage

Schema (`controlplane/db.py`, identical in SQLite and PostgreSQL):
`users`, `sessions`, `tokens` (one-time links), `sso_states`, `audit`,
`workers`, `runs`, `run_state` (latest state per run), `commands`, `claims`,
`meta`. Timestamps are 8-byte floats (`DOUBLE PRECISION`). Concurrency:
SQLite `BEGIN IMMEDIATE`; PostgreSQL row locks plus an advisory lock around
Start, so two Starts can never both succeed (tested with 10 simultaneous).

ATLAS learning stays in its append-only JSONL store. In remote mode the
control plane's `ATLAS_INTEL_DIR` is its home: the worker forwards new
event lines (deduplicated by `sync_id`) and captures (SHA-256 checked); chat
questions and feedback are recorded there. Each store carries an `ORIGIN`
marker — **production** or **test** — set by the first writer; a process of
the other origin is refused, and the ATLAS page says which it is. Snowflake
is not connected (see Remaining limitations).

## 10. Dark mode

A designed token set under `:root[data-theme="dark"]` — not an inversion:
deep navy ground `#0A1320`, cards lifted one step (`#111C2C`), cool
off-white type, **Mantrac yellow `#FFC72C`** as the one accent (active page,
primary action), and re-tuned status colours that keep their meaning at AA
(success green, human-action orange kept clear of the brand yellow, failure
red, processing blue). Light keeps the existing design. The route map,
transport illustrations, tables, drawer, chat, Human Action cards, viewer and
Access page all read the same tokens; the map's fixed SVG colours have dark
overrides. **Light / Dark / System** in the header; System follows the
device live. The choice is applied by a tiny script before first paint (no
flash), cached in `localStorage`, and — signed in — saved to the account
(`POST /api/me/prefs`) so it follows the person to any device.

## 11. Environment variables

Control plane:

| Variable | Default | |
|---|---|---|
| `PORT` / `ATA_PORT` | 8800 | App Service sets `PORT` |
| `ATA_HOST` | 127.0.0.1 | `0.0.0.0` on App Service |
| `ATA_PUBLIC_URL` | — | `https://ata.mantrac.com`; Origin check, SSO redirect, links |
| `DATABASE_URL` | `sqlite:///controlplane/data/ata.db` | `postgresql://…?sslmode=require` in Azure (Key Vault) |
| `ATA_TRUST_PROXY` | 0 | 1 behind App Service (X-Forwarded-Proto/For) |
| `ATA_INSECURE_COOKIES` | 0 | 1 only for http://127.0.0.1 development; ignored for an https public URL |
| `ATA_SESSION_IDLE_MIN` / `ATA_SESSION_MAX_HOURS` | 30 / 12 | |
| `ATA_LOGIN_ATTEMPTS` / `ATA_LOCKOUT_MIN` | 5 / 15 | |
| `ATA_LOCAL_LOGIN` | 1 | 0 to allow Microsoft sign-in only |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET` | — | all three + public URL enable SSO |
| `ENTRA_ALLOWED_DOMAINS` | — | e.g. `mantrac.com` |
| `ENTRA_AUTO_PROVISION`, `ENTRA_ROLE_CLAIMS` | 0 | |
| `ATA_WORKER_OFFLINE_S` | 30 | |
| `ATA_ALLOW_CONCURRENT_RUNS` | 0 | |
| `ATA_SESSION_LEASE_S` | 45 | Human Action claim lease |
| `ATLAS_INTEL_DIR`, `ATLAS_DATA_ORIGIN` | —, production | |

Worker: `ATA_CONTROL_PLANE_URL` (https), `ATA_WORKER_TOKEN`, `ATA_CA_FILE`
(optional private CA), `ATA_HEARTBEAT_S` (10), `ATLAS_DATA_ORIGIN`
(production), plus the automation's existing settings. Set by the agent for
the run only: `CT_RUN_ID`, `CT_SESSION_PORT`, `CT_SESSION_TOKEN`, `CT_DRY_RUN`.

## 12. Local development

```bash
cd control-tower
export ATA_INSECURE_COOKIES=1            # http://127.0.0.1 only
python -m controlplane create-admin --email you@mantrac.com --name "Your Name"
python -m controlplane add-worker --name "LOCAL-WORKER"     # prints the token
python -m controlplane serve                                # http://127.0.0.1:8800
# on the machine with Edge (can be the same one):
ATA_CONTROL_PLANE_URL=http://127.0.0.1:8800 ATA_WORKER_TOKEN=w_… python -m worker
```
The agent refuses plain HTTP except to 127.0.0.1/localhost. The local
dashboard (`START_TOWER.bat`, the run's own dashboard) is unchanged and needs
no sign-in.

Tests: `python run_tests.py`; PostgreSQL rules: `ATA_TEST_PG_URL=postgresql://…
python test_platform.py`.

## 13. Azure deployment architecture

`deploy/azure/main.bicep`: Linux App Service plan (P1v3) + Web App
(Python 3.11, `python -m controlplane serve`, Always On, HTTPS only, health
check `/healthz`, one instance, ARR affinity) with a system-assigned
identity; Key Vault (RBAC) holding the database URL and the Entra secret;
PostgreSQL Flexible Server 16 (Burstable B2s, 14-day backups, SSL);
Log Analytics + Application Insights with App Service logs; a VNet with the
Windows 11 worker VM (Trusted Launch, no public IP, NSG denying internet
inbound) and optional Azure Bastion for administrators.

## 14. Exact deployment steps

1. `az login`; `az group create -n rg-ata -l westeurope`
2. `cp deploy/azure/main.parameters.example.json main.parameters.json`; set `publicUrl`.
3. `az bicep build --file deploy/azure/main.bicep` (fix any reported API drift), then
   `az deployment group create -g rg-ata -f deploy/azure/main.bicep -p @main.parameters.json -p pgAdminPassword=… vmAdminPassword=…`
4. Entra ID (recommended): App registrations → New → name "ATA Control Tower",
   single tenant, redirect URI (Web) `https://ata.mantrac.com/auth/sso/callback`;
   Certificates & secrets → new client secret → store it:
   `az keyvault secret set --vault-name <kv> -n entra-client-secret --value …`;
   optionally App roles `ATA.Admin`, `ATA.Operator`, `ATA.Viewer`, assign people.
   Set `entraTenantId`/`entraClientId` and redeploy (step 3).
5. `python deploy/azure/package_controlplane.py` then
   `az webapp deploy -g rg-ata -n <appServiceName> --src-path dist/ata-controlplane.zip --type zip`
6. First admin, from the App Service SSH console:
   `cd /home/site/wwwroot && python -m controlplane create-admin --email you@mantrac.com --name "…"`
   (`--entra` if they sign in with Microsoft). Then
   `python -m controlplane add-worker --name ATA-WORKER-01` — copy the token.
7. Worker VM (via Bastion): install Python 3.11+, copy the control-tower
   folder (with `C:\Automation\credentials.txt` as today), run
   `.\deploy\azure\install_worker.ps1 -ControlPlaneUrl https://ata.mantrac.com -WorkerToken w_…`,
   enable auto-logon for the worker account, restart. Access → Workers shows it ONLINE.
8. Custom domain (section 15). 9. Sign in, Start automation with **dry run**
   first, confirm, then a live run.

## 15. Stable URL

1. App Service → Custom domains → Add `ata.mantrac.com`; create the CNAME
   (`ata` → `<app>.azurewebsites.net`) and the `asuid.ata` TXT record it shows.
2. Add an App Service managed certificate (or a Mantrac certificate from Key
   Vault) and bind it (SNI SSL).
3. Set `ATA_PUBLIC_URL=https://ata.mantrac.com` (Bicep parameter) — it drives
   the Origin check, the SSO redirect and the links in invitations.
4. The worker's `ATA_CONTROL_PLANE_URL` uses the same address.

## 16. Integration test results

| Suite | Result |
|---|---|
| Full suite (`run_tests.py`) | see the summary at the end of this file |
| `test_platform.py` | auth, access matrix, security, SSO, users, audit, workers, runs, human action, ATLAS, theme, stream, perf, local mode |
| `test_platform.py` with `ATA_TEST_PG_URL` (PostgreSQL 16) | all checks pass, incl. 10 simultaneous Starts → 1 run, 12 operators racing for one Human Action → 1 holder |
| `test_remote_session.py` | real worker subprocess + real Chromium + the run's own Human Action code: parked → Open & Continue → streamed → typed → verified → written + read back; timeout; agent killed → WORKER_DISCONNECTED → adopted → RUNNING; stop while held → session lost; dashboard sign-in, theme per user, viewer, roles |

## 17. Security test results

Covered in `test_platform.py` §2–§6, §8 and `test_remote_session.py` §3:
every protected route × Admin/Operator/Viewer/anonymous; missing or forged
CSRF token and foreign Origin refused; CSP with no `unsafe-inline` scripts and
no inline handlers left; nosniff/DENY/Referrer-Policy; `__Host-` Secure
cookie + HSTS over HTTPS; sign-in rate limit; path traversal; oversized
bodies; SQL-shaped input; worker token ≠ user session; forged worker token;
tokens and passwords stored hashed; Entra tokens: tampered body, wrong
audience, wrong tenant, expired, wrong nonce, `alg: none`, foreign domain,
reused state — all refused; audit metadata scrubbing; the security code
typed through the live view found nowhere afterwards.

## 18. Performance results

* Dashboard screen refresh (`paint()`, 120 shipments, measured in Chromium,
  net of state copying): before this change ≈0.25–0.30 ms, after ≈0.29–0.31 ms
  in local mode (run-to-run noise; baseline 0.34 ms). Remote mode: measured in
  `test_remote_session.py` (budget 1 ms).
* Control-plane state for 300 shipments: built in a few ms (budget 25 ms,
  `test_platform.py` §12); unchanged shipments are omitted from SSE frames as
  before.
* Worker report → open dashboard over SSE: measured < 500 ms (test budget).
* Platform UI work is gated by `changed()` like the rest of the page; nothing
  polls faster than the existing stream (Access page: 15 s while open).

## 19. Remaining limitations

* **Not deployed.** No Azure subscription is available here: the Bicep has not
  been compiled (`az bicep build`) or deployed, and the App Service, Key Vault
  references, PostgreSQL Flexible Server, custom domain and certificate are
  untested in Azure. The packaged app was started standalone and works.
* **Entra ID** was tested against a locally generated RSA key and simulated
  Microsoft endpoints, not against a real tenant.
* **Real carriers and the real Hub** were not reached: the end-to-end tests
  use a GNET-like stand-in page and an in-memory Hub. Edge on Windows was not
  available; Chromium (same CDP) was used. The first live remote session on
  the worker should be watched.
* **One App Service instance.** The live-view relay and the rate limiter are
  in process memory; scale-out needs a shared bus (e.g. Azure Web PubSub or
  Redis) first. Claims, runs and audit are already in the database.
* **Clipboard paste** into the remote view is not supported (typing is).
* **The worker token** is a long random bearer secret over TLS; mutual TLS or
  Entra workload identity for the worker is a later hardening step.
* **Snowflake** is not connected; the control plane's PostgreSQL is the
  operational store. An export job can be added when credentials exist.
* **Email**: invitations are links an admin hands over; nothing is emailed.
* **ATLAS proposals** have PROPOSED → APPROVED/REJECTED in the product; TEST
  and VERIFY stages happen in engineering, and DEPLOY is a code change — by
  design nothing deploys itself. `atlas_review.bat` still works on the store
  of the machine it runs on.
* The learning store starts **empty in production**; earlier learning
  screenshots used test data, and test stores are labelled TEST.

## 20. Manual steps required from you

1. An Azure subscription and a resource group; run steps 1–5 of §14.
2. DNS for `ata.mantrac.com` (CNAME + TXT) and the certificate (§15).
3. Decide sign-in: Entra ID app registration (recommended; IT admin consent),
   or local accounts only (`ATA_LOCAL_LOGIN=1`).
4. Create the first admin (§14 step 6) and then invite people from Access.
5. Register the worker, put its token on the VM, run `install_worker.ps1`,
   set auto-logon for the worker account; copy `credentials.txt` as today.
6. First run: **dry run** from Start automation; watch the first remote Human
   Action end to end on the worker, then switch to live runs.
7. Keep scale-out at one instance until a shared relay is added.
