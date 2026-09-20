# 02 — End-to-End Sequence Flows

## 2.1 Happy path — cache miss, SIS2 lookup succeeds

```
User      Maya(LLM)     maya-api        Freshness   Repo(Snowflake)   Worker/Playwright   SIS2
 │  "دوريلي على المعدة SN123456"
 ├────────►│
 │         │ extract serial → "SN123456" (regex-validated server-side too)
 │         ├─ tool: get_equipment_from_database(serial_number)
 │         │        ├──────────────►│
 │         │        │               ├─ SELECT current row ──────►│
 │         │        │               │◄──── not found ────────────┤
 │         │        │◄── {found:false, reason:"NOT_IN_STORE"} ───┤
 │         │◄───────┤
 │         ├─ tool: search_equipment_in_sis(serial_number)
 │         │        ├──────────────►│ create automation_run (status=RUNNING)
 │         │        │               ├─ enqueue job ─────────────────►│
 │         │        │               │                                ├─ acquire context (warm session)
 │         │        │               │                                ├─ step: ENSURE_SESSION ──────►│
 │         │        │               │                                │◄── 200 /#/ authenticated ────┤
 │         │        │               │                                ├─ step: SEARCH_SERIAL ───────►│
 │         │        │               │                                │◄── result list / detail ─────┤
 │         │        │               │                                ├─ step: EXTRACT_RAW
 │         │        │               │◄── raw payload + artifacts ────┤
 │         │        │               ├─ normalize()  → canonical JSON
 │         │        │               ├─ validate()   → quality_score, violations=[]
 │         │        │               ├─ MERGE equipment_data + INSERT history
 │         │        │               └─ complete run (status=SUCCESS, ms=18240)
 │         │◄── {status:"success", data:{...}, source, retrieved_at, run_id} ─┤
 │◄── Arabic answer incl. Source + Retrieved + Run ID
```

## 2.2 Cache hit (fresh)

```
User → Maya → get_equipment_from_database("SN123456")
                 └─ repo returns row (retrieved_at = 3 days ago)
                    freshness.evaluate(row, policy) → FRESH (ttl 30d for CAT SIS)
              ← {found:true, freshness:"FRESH", age_days:3, data:{...}}
Maya answers from the store, states "من الـ internal data store، آخر سحب من SIS بتاريخ …".
No browser is launched. Expected ≥90 % of production traffic.
```

## 2.3 Stale cache → refresh, SIS down → serve stale with an explicit warning

```
get_equipment_from_database → found, age 210d, policy ttl 30d → STALE
Maya → search_equipment_in_sis(serial, reason="STALE_REFRESH")
        worker → attempt 1 TIMEOUT → attempt 2 TIMEOUT → attempt 3 NETWORK_ERROR
        run status = FAILED, error_code = TIMEOUT
   ← {status:"error", error_code:"TIMEOUT", fallback:{available:true, age_days:210, data:{...}}}
Maya: "مقدرتش أوصل لـ SIS دلوقتي (Timeout). عندي نسخة محفوظة عمرها 210 يوم — أعرضها؟"
      Stale data is ALWAYS labelled with its age and never presented as current.
```

## 2.4 Serial not found in SIS

```
worker: SEARCH_SERIAL → zero-results state detected (explicit empty-state marker,
                        not "selector missing" — see docs/06)
run status = FAILED, error_code = SERIAL_NOT_FOUND, a negative-cache row is written
   with status = NOT_FOUND and ttl = negative_cache_ttl_days (default 7)
Maya: "السيريال SN123456 مش موجود في Caterpillar SIS. اتأكد من الرقم أو ابعتلي الـ PIN كامل."
No data object is emitted, so there is nothing for the model to paraphrase.
```

## 2.5 Session expiry mid-run (self-healing)

```
step SEARCH_SERIAL → adapter detects redirect to Cat Login (CWS)
   → classify SESSION_EXPIRED (retryable, idempotent)
   → invalidate stored storage_state for that session slot
   → step ENSURE_SESSION performs full interactive login (from Vault secret)
   → replay SEARCH_SERIAL once
   → success; run records steps: [ENSURE_SESSION, SEARCH_SERIAL(fail:SESSION_EXPIRED),
                                   RELOGIN, SEARCH_SERIAL(ok), EXTRACT_RAW]
```

## 2.6 CAPTCHA / bot challenge (hard stop, never bypassed)

```
adapter detects challenge marker → error_code = CAPTCHA_DETECTED
   → retry is NOT attempted (retryable=false); circuit breaker for the source opens
   → ops alert `sis.captcha` fires; run FAILED with artifact screenshot reference
Maya: "فيه تحقق أمني على SIS محتاج تدخل بشري. فتحت تذكرة للفريق (Run ID …)."
We do not solve, bypass, or automate around anti-bot controls — this is a
contractual and legal boundary, and the platform is built to stop cleanly.
```

## 2.7 Concurrency / de-duplication

```
Two users ask for SN123456 within 4 s.
  request A → idempotency key = sha256("cat_sis|SN123456")
              → no in-flight run → creates run R1, acquires distributed lock
  request B → same key → finds in-flight R1 → returns {status:"in_progress",
              automation_run_id:R1} instead of launching a second browser.
Both answers are served from R1's result. One browser, one SIS hit.
```
