"""Why Cortex cannot run, in words an administrator can act on.

Every failure here becomes CORTEX_UNAVAILABLE with a `reason` code and a
`fix`. There is no fallback to another model: an unanswered question is
reported as exactly that.
"""
from __future__ import annotations

from typing import Any

from app.core.errors import AutomationError, ErrorCode

FIXES: dict[str, str] = {
    "disabled": "Set MAIA_CORTEX_ENABLED=true (or SNOWFLAKE_CORTEX_ENABLED=true).",
    "not_configured": "Configure the Snowflake connection (snowflake.txt or SNOWFLAKE_* "
                      "variables): account, user, a sign-in method, warehouse, database, schema.",
    "driver_missing": "Install the Snowflake driver: pip install snowflake-connector-python.",
    "agent_auth": "The Cortex Agents REST API needs a bearer token: set a programmatic access "
                  "token (pat= in snowflake.txt / SNOWFLAKE_PAT) or a key pair "
                  "(private_key_file=). SSO and passwords work with MAIA_CORTEX_MODE=complete.",
    "privilege": "Grant the Cortex database role to Maia's role: "
                 "GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE <role>; "
                 "(for agents, SNOWFLAKE.CORTEX_AGENT_USER also works).",
    "function_missing": "AI_COMPLETE is not visible to Maia's role. Usually the role lacks "
                        "the Cortex database role (GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER "
                        "TO ROLE <role>;); otherwise Cortex is not offered in this "
                        "account/region.",
    "model_unavailable": "The model is not available in this region. Choose another with "
                         "MAIA_CORTEX_MODEL, or ask an admin to set "
                         "ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';",
    "auth_rejected": "Snowflake rejected the credentials for the Cortex call. Check the PAT or "
                     "the key pair registered on the user (RSA_PUBLIC_KEY).",
    "agent_api_missing": "The Cortex Agents API is not available to this account. Use "
                         "MAIA_CORTEX_MODE=complete, or ask Snowflake to enable Cortex Agents.",
    "network": "Snowflake could not be reached from this machine (network / VPN / proxy).",
    "bad_response": "Cortex returned a response Maia could not read.",
    "quota": "Cortex rejected the request for rate or budget reasons; try again later.",
    "unknown": "Cortex returned an error; see `detail`.",
}


def unavailable(reason: str, detail: str = "", **extra: Any) -> AutomationError:
    return AutomationError(
        ErrorCode.CORTEX_UNAVAILABLE,
        f"Snowflake Cortex is unavailable: {reason}.",
        details={"reason": reason, "fix": FIXES.get(reason, FIXES["unknown"]),
                 "detail": detail[:300], **extra})


def classify_sql_error(text: str) -> str:
    """Map a driver error text to a reason. Order matters: most specific first."""
    t = (text or "").lower()
    if "unknown function" in t or "unknown user-defined function" in t \
            or "invalid identifier 'ai_complete'" in t:
        return "function_missing"
    if "insufficient privileges" in t or "not authorized" in t or "access denied" in t:
        return "privilege"
    if "unavailable in your region" in t or "not available in your region" in t \
            or "cross-region" in t or ("model" in t and "not available" in t) \
            or "unknown model" in t or "invalid model" in t:
        return "model_unavailable"
    if "quota" in t or "rate limit" in t or "too many requests" in t or "budget" in t:
        return "quota"
    if "could not connect" in t or "timed out" in t or "connection" in t:
        return "network"
    if "incorrect username or password" in t or "jwt" in t or "authentication" in t:
        return "auth_rejected"
    return "unknown"


def classify_http(status: int, body: str) -> str:
    if status in (401,):
        return "auth_rejected"
    if status == 403:
        return "privilege"
    if status == 404:
        return "agent_api_missing"
    if status == 429:
        return "quota"
    return classify_sql_error(body) if body else "unknown"
