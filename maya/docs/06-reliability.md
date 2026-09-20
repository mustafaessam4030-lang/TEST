# 06 — Error Taxonomy, Retry & Circuit Breaking

## 6.1 Taxonomy

| Code | HTTP | Retryable | Detection | Maya says (substance) |
|---|---|---|---|---|
| `INVALID_SERIAL` | 400 | no | server-side regex/checksum | "That doesn't look like a serial — check it?" |
| `SERIAL_NOT_FOUND` | 404 | no | explicit empty-state marker | "SIS has no record for it." + negative-cached 7d |
| `LOGIN_FAILED` | 502 | no* | credential rejected | "Our SIS access is failing — ops alerted." |
| `SESSION_EXPIRED` | — | yes (internal) | redirect to login host | invisible; self-healed |
| `CAPTCHA_DETECTED` | 503 | no | challenge marker | "Security check needs a human. Ticket opened." |
| `WEBSITE_CHANGED` | 502 | no | required selector/ready-marker absent | "SIS changed layout; engineering alerted." |
| `TIMEOUT` | 504 | yes (2) | step deadline exceeded | "SIS didn't answer in time." |
| `NETWORK_ERROR` | 503 | yes (2) | DNS/TLS/reset | "Couldn't reach SIS." |
| `RATE_LIMITED` | 429 | yes (backoff) | source 429 / our own quota | "Too many lookups; try in N min." |
| `EXTRACTION_ERROR` | 502 | yes (1) | required fields missing | "Reached the record but couldn't read it." |
| `INVALID_DATA` | 422 | no | schema/business validation failed | "Data failed our quality checks; not saving it." |
| `DATABASE_ERROR` | 503 | yes (2) | warehouse failure | "Retrieved it but couldn't store it." (data still returned, flagged unsaved) |
| `CIRCUIT_OPEN` | 503 | no | breaker open | "SIS lookups paused after repeated failures." |
| `INTERNAL_ERROR` | 500 | no | unhandled | generic + run id |

`LOGIN_FAILED` is marked non-retryable *for the request* on purpose: repeatedly
retrying a rejected credential is how you get an account locked at the partner.
It retries once at most, then opens the breaker and pages ops.

## 6.2 Retry policy (server-side only — the model never retries)

```
attempt_delay = min(base * 2**(n-1), cap) * jitter(0.5..1.5)
base = 2 s, cap = 30 s, attempts by code:
  TIMEOUT / NETWORK_ERROR / DATABASE_ERROR → 3 total
  EXTRACTION_ERROR                          → 2 total (2nd forces a fresh context)
  SESSION_EXPIRED                           → 1 relogin + 1 replay (not counted)
  RATE_LIMITED                              → honour Retry-After, max 2
  everything else                           → 0
```

Rules:
* **Idempotency first.** A retry is only allowed for steps declared idempotent
  (all read steps are; any future write step is not).
* **Escalating isolation.** Retry 1 reuses the context; retry 2 gets a brand-new
  context and a forced relogin. Most "flaky" SIS failures are stale client state.
* **Total wall clock is bounded** by `run_deadline_ms` (default 90 s). The
  deadline, not the attempt count, is the real limit.
* Maya is told *once* about the outcome, never about the attempts, except in the
  trace panel where the step list is shown verbatim.

## 6.3 Circuit breaker (per source)

```
CLOSED → (5 non-retryable failures OR 40% failure rate over 20 runs in 10 min) → OPEN
OPEN   → all search calls fail fast with CIRCUIT_OPEN (cache reads keep working)
OPEN   → after cooldown 5 min → HALF_OPEN (1 canary run) → success ⇒ CLOSED, failure ⇒ OPEN (cooldown ×2, cap 60 min)
```

`CAPTCHA_DETECTED` and `LOGIN_FAILED` open the breaker immediately — they are
account-safety events, not blips.

Crucially, an open breaker degrades to **store-only mode**: cached answers keep
flowing with honest age labels. The Control Tower keeps working while SIS is
unreachable.

## 6.4 Degradation ladder

| Condition | Behaviour |
|---|---|
| SIS slow | 202 + run id, Maya offers to report back |
| SIS down, fresh cache exists | serve cache silently (it was valid anyway) |
| SIS down, stale cache exists | serve with explicit age + "couldn't refresh" |
| SIS down, no cache | **no data** — explain the failure, offer ticket/hotline |
| Warehouse down, SIS up | serve retrieved data, mark `persisted:false`, queue write |
| Both down | apologise, give run id, open ticket |

The bottom row is the one that matters: with no cache and no source, the correct
output is an explanation, never a plausible-looking machine spec.
