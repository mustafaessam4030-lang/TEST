"""Snowflake Cortex — Maia's runtime intelligence.

    Maia ─▶ FastAPI gateway ─▶ Cortex (Agents REST, or AI_COMPLETE over SQL)
                  ▲                         │ asks for a tool
                  └──── executes it ◀───────┘ (never Playwright, never SQL)

Nothing in this package calls any other LLM provider.
"""
