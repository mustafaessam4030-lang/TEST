# What's in this ZIP

Two things, and they fit together: **Maia (the chat UI)** and **the automation
platform she calls**.

```
maia/
├── frontend/                     ← MAIA, THE CHAT
│   ├── mantrac-support-v8.html     your original, untouched
│   ├── mantrac-support-v9.html     ★ v8 + the equipment capability — open this
│   ├── maia-equipment.js           the tool layer that was added to her
│   ├── build_v9.py                 rebuilds v9 from v8 (additive patcher)
│   └── test_equipment.mjs          23 behavioural checks
│
├── services/automation/          ← THE AUTOMATION (FastAPI + Playwright)
│   ├── app/api/                    HTTP surface
│   ├── app/domain/                 normalize · validate · freshness
│   ├── app/adapters/               SIS2 adapter, browser pool, selector health
│   ├── app/repositories/           in-memory + Snowflake
│   ├── app/services/               the decision flow + run recorder
│   └── tests/                      56 tests
│
├── scripts/capture/              ← SELECTOR CAPTURE (the remaining blocker)
│   ├── capture_selectors.py        headed, human-in-the-loop capture tool
│   ├── picker.js                   click-to-pick overlay + candidate builder
│   └── fixture.html                offline SIS-shaped page for the self-test
│
├── docs/01…11                    architecture → runbook (start at README.md)
├── sql/                          Snowflake + Databricks schemas
├── schemas/                      equipment JSON Schema + Maia's tool definitions
├── agent/                        system prompt, tool dispatcher, agent loop
├── config/                       freshness policy, SIS source contract, .env.example
└── artifacts/capture/            proof: screenshots + report from the self-test run
```

## Try it in two minutes (no backend needed)

Open `frontend/mantrac-support-v9.html` in a browser. Maia works exactly as
before; ask her for a serial and she will say the equipment service is not
connected — she never invents data.

## Run the backend

```bash
pip install -r services/automation/requirements-dev.txt
python -m playwright install chromium
make test                 # 56 backend + 23 frontend + the fixture capture run
make run                  # API on :8080, live automation OFF by default
```

Then set `CFG.equipment.apiBase` near the top of the v9 HTML to your API origin.

## The one blocker

`config/sis_selectors.json` does not exist yet, on purpose. No SIS selector in
this package is guessed. Capture them in ~10 minutes on a machine with SIS
access and a screen:

```bash
make capture SERIAL=<a serial that exists in SIS>
```

Full procedure: `docs/11-selector-capture-runbook.md`.

## Security note

No credentials are in this package, and none ever should be. The automation
reads them from your secret store (`vault://…`) or environment (`env://…`) at
the moment of use. If a password was ever shared over chat or email, rotate it.
