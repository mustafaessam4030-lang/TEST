"""
What ATLAS knows without looking anything up — written down, so it can be
read, corrected and tested.

GLOSSARY. Plain definitions of the logistics and customs terms operators
ask about ("What is transshipment?", "ETA vs ATA?", "What does Under
Clearance mean?"). General knowledge, stated as such.

ERROR INVESTIGATION. For a failure the run recorded, the possible causes,
ranked against the run's own evidence:

    CONFIRMED   the run's evidence proves it
    LIKELY      the evidence points to it, but does not prove it
    POSSIBLE    consistent with the evidence; nothing yet favours it
    UNKNOWN     cannot be judged from this run

Each with why it fits, the safest diagnostic step, the fix, and how to
verify the fix. A ranking here is reasoning about evidence, never a verified
root cause: only a recovery that was applied and then verified by the run
is ever learned as one (intelligence/learning).
"""

import re

CONFIRMED, LIKELY, POSSIBLE, UNKNOWN = "CONFIRMED", "LIKELY", "POSSIBLE", "UNKNOWN"
ORDER = {CONFIRMED: 0, LIKELY: 1, POSSIBLE: 2, UNKNOWN: 3}

# ── glossary ──────────────────────────────────────────────────────────────
# (pattern, title, answer). Patterns match the term as an operator writes it.
GLOSSARY = [
    (r"trans-?shipment", "Transshipment",
     "Transshipment is when cargo is unloaded from one vessel at an intermediate port and "
     "loaded onto another vessel to continue to its destination — typical at hub ports where "
     "a mainline service connects to a feeder. It adds a connection that can slip: if the "
     "second vessel is missed or delayed, the ETA moves even though the first leg ran on time."),
    (r"blank(ed)? sailing", "Blank sailing",
     "A blank sailing is a scheduled voyage the carrier cancels — the vessel skips a port or "
     "the whole rotation for that week. Carriers do it to manage capacity or recover schedules. "
     "Cargo booked on it is rolled to the next available sailing, so its ETA moves by roughly "
     "the service's frequency (often a week)."),
    (r"demurrage", "Demurrage",
     "Demurrage is the charge for leaving a container inside the port/terminal beyond the free "
     "days allowed. It is different from detention, which is charged for keeping the carrier's "
     "container outside the terminal (at your premises) beyond its free time. Delays in customs "
     "clearance are a common cause of demurrage."),
    (r"detention", "Detention",
     "Detention is the charge for keeping the carrier's container outside the terminal beyond the "
     "free time — for example while it is being unloaded at your warehouse. Demurrage is the "
     "equivalent charge while the container is still in the port."),
    (r"\beta\b.*\bata\b|\bata\b.*\beta\b|estimated time of arrival|actual time of arrival",
     "ETA vs ATA",
     "ETA is the Estimated Time of Arrival — the carrier's current forecast, which can change. "
     "ATA is the Actual Time of Arrival — when the vessel or flight really arrived, recorded "
     "after the fact. In this Control Tower the run reads both from the carrier and writes the "
     "ETA (and the ATA when there is one) to the Hub."),
    (r"\betd\b.*\batd\b|\batd\b.*\betd\b|estimated time of departure|actual time of departure",
     "ETD vs ATD",
     "ETD is the Estimated Time of Departure from the origin port or airport; ATD is the Actual "
     "Time of Departure, recorded once it has left."),
    (r"under clearance", "Under Clearance",
     "In eHub, Under Clearance is the shipment status meaning the goods have arrived (or are "
     "arriving) and customs clearance is in progress — declaration, assessment, duty payment "
     "and release. It is the status this Control Tower works on: the ETA automation updates "
     "Under Clearance shipments, and the PO automation processes the Bill of Entry of those "
     "records."),
    (r"bill of entry|\bboe\b", "Bill of Entry",
     "A Bill of Entry is the customs declaration for imported goods: it lists the goods, their "
     "value, and the duties and taxes assessed. The PO automation reads the Bill of Entry "
     "attached to an Under Clearance record in eHub to calculate and prepare the duty PO."),
    (r"bill of lading|\bb/?l\b|\bbol\b", "Bill of lading",
     "A bill of lading (B/L) is the document a sea carrier issues for a shipment: a receipt for "
     "the cargo, evidence of the contract of carriage and, when negotiable, title to the goods. "
     "Its number is the reference used to track an ocean shipment with the carrier."),
    (r"air ?way ?bill|\bawb\b", "Air waybill",
     "An air waybill (AWB) is the transport document for air cargo. Its 11-digit number starts "
     "with the airline's 3-digit prefix (for example 057 for Air France, 074 for KLM, 157 for "
     "Qatar Airways), which is how the automation knows which airline to ask."),
    (r"\bimo\b", "IMO number",
     "The IMO number is a permanent 7-digit identifier assigned to a seagoing ship; it stays the "
     "same when the ship is renamed or changes owner, so it is the reliable way to identify a "
     "vessel."),
    (r"voyage", "Voyage number",
     "A voyage number identifies one specific sailing of a vessel on a service. The same vessel "
     "makes many voyages; the vessel name plus voyage number pins down the sailing your cargo "
     "is on."),
    (r"roll(ed)?[- ]?over|rolled cargo|\brolled\b", "Rolled cargo",
     "Cargo is 'rolled' when it was booked on a sailing but is moved to a later one — because "
     "the vessel was full, the sailing was blanked, or the cargo missed the cut-off. The ETA "
     "moves accordingly."),
    (r"port congestion|congestion", "Port congestion",
     "Port congestion is when vessels have to wait at anchor because berths or yard capacity "
     "are full. Waiting time adds directly to arrival and discharge, so ETAs slip; it also slows "
     "container availability after discharge."),
    (r"why (do|are) (vessels?|ships?) (get )?delay|vessel delays?|ship delays?",
     "Why vessels get delayed",
     "The usual reasons are port congestion (waiting for a berth), weather, missed or delayed "
     "transshipment connections, blank sailings and schedule recovery, canal or strait "
     "disruptions, equipment or crew issues, and slow-steaming to save fuel. The carrier's "
     "schedule updates and port notices usually say which applies."),
    (r"\bcif\b|\bcfr\b|incoterm", "CIF / CFR",
     "CIF (Cost, Insurance and Freight) and CFR (Cost and Freight) are Incoterms: the seller "
     "pays the main carriage to the destination port; under CIF the seller also insures the "
     "goods. Customs duty in many countries, including Ghana, is assessed on the CIF value."),
    (r"free time", "Free time",
     "Free time is the number of days the carrier or terminal allows before demurrage or "
     "detention charges start."),
    (r"feeder", "Feeder vessel",
     "A feeder is a smaller vessel that carries containers between a hub port and smaller ports "
     "the mainline ships do not call at — the second leg of a transshipment."),
    (r"\bmanifest\b", "Cargo manifest",
     "The manifest is the carrier's list of all cargo on a vessel or flight, filed with customs "
     "before arrival so the goods can be declared and cleared."),
]
GENERAL_ASK = re.compile(
    r"^\s*(what('?s| is| are| does| do)|define|explain|meaning of|how does|how do|why do|"
    r"difference between|tell me what)\b", re.I)


def glossary(question):
    """(title, answer) for a general definition question, or None."""
    text = " ".join(str(question or "").split())
    if not GENERAL_ASK.search(text):
        return None
    for pattern, title, answer in GLOSSARY:
        if re.search(pattern, text, re.I):
            return title, answer
    return None


# ── error investigation ──────────────────────────────────────────────────

def _cause(status, cause, why, check, fix, verify):
    return {"status": status, "cause": cause, "why": why, "check": check, "fix": fix,
            "verify": verify}


def _text(f):
    return " ".join([str(f.get("error_message") or ""), str(f.get("first_failing_event") or ""),
                     str((f.get("observed_state") or {}).get("outcome") or "")])


def investigate_failure(f, run_records=None):
    """
    Ranked causes for one failure record (intelligence/failures.build).
    Returns {"observed", "causes": [...], "never": [...]}.
    """
    category = f.get("classification") or f.get("error_type") or "UNKNOWN_FAILURE"
    carrier = f.get("carrier") or "the carrier"
    text = _text(f)
    low = text.casefold()
    same_carrier = [r for r in (run_records or []) if (r.get("carrier") or "").casefold() ==
                    str(f.get("carrier") or "").casefold()]
    failed_same = [r for r in same_carrier if r.get("state") in ("failed", "partial", "skipped")]
    ok_same = [r for r in same_carrier if r.get("state") == "updated"]
    causes = []
    observed = f.get("headline") or f.get("classification_label") or category
    never = []

    if category in ("CARRIER_ACCESS_RESTRICTED", "CARRIER_ACCESS_NOT_CONFIRMED"):
        never = ["retrying the lookup against the restriction", "rotating IPs or using "
                 "residential proxies", "trying to defeat the carrier's bot detection",
                 "automating the human verification"]
        causes.append(_cause(
            LIKELY, "An access-level restriction on how the worker reaches {0} — not a problem "
            "with the shipment itself".format(carrier),
            "The restriction page came back instead of the shipment, and it stayed after the human "
            "verification was completed; nothing on it concerns this reference.",
            "Open {0}'s tracking page by hand on the worker PC, same network, outside the "
            "automation.".format(carrier),
            "Depends on what the check shows (below).",
            "The same lookup shows the shipment page, and the run reads it."))
        causes.append(_cause(
            POSSIBLE, "The worker's network path — its public IP, VPN, proxy or corporate "
            "security gateway — is being refused",
            "Carriers commonly restrict by network reputation; a corporate egress or VPN can be "
            "flagged. Nothing in the run proves or rules it out.",
            "python -m worker.verify carrier --carrier {0} --reference {1} (compares the "
            "automation with a manual browser and records the network)".format(
                f.get("provider") or "CARRIER", f.get("shipment_id") or "REF"),
            "If only this network is refused: ask IT to check the proxy/VPN/gateway for {0}, or "
            "contact {0} with the worker's public IP. Do not switch networks to evade it.".format(
                carrier),
            "The carrier diagnostic shows the page loading on the worker's network."))
        causes.append(_cause(
            LIKELY if len(failed_same) > 1 else POSSIBLE,
            "Request volume — several lookups to {0} in a short time".format(carrier),
            "{0} {1} lookups in this run did not complete.".format(len(failed_same), carrier)
            if len(failed_same) > 1 else "Only this lookup is recorded as refused in this run.",
            "Look at how many {0} lookups the run made and how close together.".format(carrier),
            "Space {0} lookups out (the run's bounded pacing), never retry immediately.".format(
                carrier),
            "The next run's {0} lookups are not refused.".format(carrier)))
        causes.append(_cause(
            POSSIBLE, "The automated browser session or profile is what is refused",
            "If a manual browser on the same network works while the automation does not, the "
            "session/profile is the difference.",
            "The same carrier diagnostic: it runs both and compares.",
            "Use the run's normal browser profile and settings; a person decides on any change. "
            "No evasion of the carrier's detection.",
            "The automation's own lookup shows the shipment page."))
        causes.append(_cause(
            UNKNOWN, "A carrier-side incident or maintenance",
            "Nothing in the run shows the state of {0}'s site for other users.".format(carrier),
            "Check {0}'s official customer advisories.".format(carrier),
            "Wait for the carrier; nothing to change on our side.",
            "Other users/networks reach the page normally again."))
        if ok_same:
            causes[1]["why"] += " {0} other {1} lookup(s) in this run did complete, which " \
                "weighs against a blanket network block.".format(len(ok_same), carrier)
    elif category in ("SECURITY_VERIFICATION_REQUIRED", "HUMAN_ACTION_REQUIRED"):
        never = ["solving or automating the verification", "reading or typing security codes"]
        causes.append(_cause(
            CONFIRMED, "{0} requires a person to complete a verification step".format(carrier),
            "The run recorded the verification page.",
            "Open & Continue from the Human Action queue.",
            "A person completes the step; the run continues and verifies.",
            "The run reads the shipment and the Hub read-back matches."))
    elif category == "CARRIER_POLICY_BLOCK":
        cause = f.get("declared_cause") or {}
        causes.append(_cause(
            CONFIRMED, "A rule of this automation stopped it: {0}".format(
                cause.get("name") or "a configured policy"),
            "Declared by the run's own code at the point it stopped.",
            "Read the setting named in the run log.",
            "Change the rule only if the business decides to; it is configuration, not a fault.",
            "The next run applies the new rule."))
    elif category in ("NETWORK_FAILURE", "NAVIGATION_FAILURE", "TIMEOUT", "PAGE_NOT_READY"):
        proxy = re.search(r"err_proxy|proxy", low)
        dns = re.search(r"err_name_not_resolved|getaddrinfo|name or service not known", low)
        tls = re.search(r"err_cert|ssl|certificate", low)
        refused = re.search(r"err_connection_(refused|reset|closed)|econnreset|econnrefused", low)
        if proxy:
            causes.append(_cause(
                LIKELY, "The worker's proxy could not be used (ERR_PROXY…)",
                "The browser reported a proxy error before reaching {0}.".format(carrier),
                "Check the proxy settings on the worker (system/Edge) and that the proxy host "
                "answers.", "Correct the proxy configuration with IT.",
                "The carrier page loads in the worker's browser."))
        if dns:
            causes.append(_cause(
                LIKELY, "Name resolution failed on the worker (DNS)",
                "The browser could not resolve the carrier's host name.",
                "nslookup the carrier host on the worker.", "Fix DNS / network with IT.",
                "The host resolves and the page loads."))
        if tls:
            causes.append(_cause(
                LIKELY, "A TLS/certificate problem between the worker and {0}".format(carrier),
                "The error names a certificate/SSL failure — often a corporate TLS inspection "
                "gateway.", "Open the page in Edge on the worker and look at the certificate "
                "warning.", "IT adds the gateway's certificate or exempts the host.",
                "The page loads without a certificate error."))
        if refused:
            causes.append(_cause(
                LIKELY, "The connection was refused or reset",
                "The error says the connection was refused/reset.",
                "Check whether {0}'s site is up from another network.".format(carrier),
                "Wait if the carrier is down; check firewall rules if only the worker is "
                "affected.", "The page loads."))
        if category in ("TIMEOUT", "PAGE_NOT_READY"):
            causes.append(_cause(
                LIKELY if len(failed_same) > 1 else POSSIBLE,
                "The page layout changed, so the element the automation waits for never appears",
                "{0} {1} lookups failed the same way in this run.".format(
                    len(failed_same), carrier) if len(failed_same) > 1 else
                "Only one lookup failed this way, so a layout change is not established.",
                "Compare the kept screenshot with the expected page.",
                "Update the page reader through a tested change — never in production directly.",
                "A test against the new layout passes, then a live lookup reads the date."))
            causes.append(_cause(
                POSSIBLE, "An interstitial covered the page (cookie banner, notice, verification)",
                "Timeouts often come from a page that did load but shows something in front.",
                "Look at the screenshot kept with the failure.",
                "If it is a verification step, it goes to the Human Action queue.",
                "The next lookup reaches the shipment."))
        causes.append(_cause(
            POSSIBLE, "{0}'s site was slow or briefly unavailable".format(carrier),
            "A transient slowness produces exactly this; {0}.".format(
                "other lookups to the same carrier completed" if ok_same else
                "no other lookup to the same carrier is there to compare"),
            "Let the run's work list look it up again later — bounded, never in a tight loop.",
            "Nothing to change if a later lookup works.",
            "The later lookup completes and verifies."))
        if category == "NAVIGATION_FAILURE":
            causes.append(_cause(
                POSSIBLE, "{0} changed its tracking page or address".format(carrier),
                "The page the automation expected did not open; a changed URL or flow does that.",
                "Open the carrier's tracking page by hand and compare with the kept screenshot.",
                "Update the navigation through a tested change.",
                "A test plus a live lookup reach the shipment."))
            if not (proxy or dns or tls or refused):
                causes.append(_cause(
                    POSSIBLE, "Something on the worker's network path (proxy, firewall, DNS)",
                    "The error does not name a network cause, so this is not established.",
                    "Open the carrier site in Edge on the worker PC.",
                    "Fix with IT only if the manual check fails too.",
                    "The page loads on the worker."))
    elif category == "AUTHENTICATION_FAILURE":
        causes.append(_cause(
            LIKELY, "The stored credentials no longer work (changed, expired or locked)",
            "Sign-in was refused.", "Sign in by hand with the same account.",
            "Update the credentials in the worker's credential file (never in chat or logs).",
            "The next run signs in."))
    elif category == "DATA_EXTRACTION_FAILURE":
        causes.append(_cause(
            POSSIBLE, "{0} has not published a date for this shipment yet".format(carrier),
            "The page loaded but held no usable date.", "Look at the kept page text.",
            "Nothing to fix — the next run reads it once published.",
            "A later run reads a date."))
        causes.append(_cause(
            POSSIBLE, "The page's layout or wording changed",
            "A date may be there in a form the reader does not recognise.",
            "Compare the kept page text with what the reader expects.",
            "Update the reader through a tested change.", "A test plus a live read pass."))
    elif category in ("HUB_WRITE_FAILURE", "HUB_READBACK_FAILURE"):
        causes.append(_cause(
            POSSIBLE, "The eHub form refused or did not keep the value (validation, locked "
            "field, session)", "The write or its read-back did not match.",
            "Open the record in eHub and look at the field.",
            "Correct in eHub by hand if needed; the run never claims a write it did not read "
            "back.", "The read-back equals the value written."))
    graph = re.search(r"graph|mail\.send|sendmail", low)
    if graph and re.search(r"\b403\b|forbidden|access ?denied", low):
        causes.insert(0, _cause(
            LIKELY, "The Microsoft Graph app is missing Mail.Send (application) permission, its "
            "admin consent, or is limited by an application access policy for this mailbox",
            "Graph answered 403 to the send request.",
            "In Entra ID, check the app's API permissions (Mail.Send, Application) and admin "
            "consent; check any application access policy on the sender mailbox.",
            "An Entra admin grants/consents or adjusts the policy — a human-approved change.",
            "A controlled test send is accepted (202) and found in Sent Items."))
    elif graph and re.search(r"\b401\b|unauthori[sz]ed|invalidauthenticationtoken", low):
        causes.insert(0, _cause(
            LIKELY, "The Graph app credentials were rejected (secret expired or wrong tenant/"
            "client id)", "Graph answered 401.",
            "Check the client secret's expiry in Entra ID.",
            "Rotate the secret and store it in the worker's secret store — never in chat.",
            "A controlled test send is accepted."))
    if not causes:
        causes.append(_cause(
            UNKNOWN, "Not enough evidence in this run to rank causes",
            "The run recorded: {0}".format(text[:200] or "no error text"),
            "Look at the screenshot and log kept with the failure.",
            "None proposed without evidence.", "—"))
    causes.sort(key=lambda c: ORDER[c["status"]])
    return {"observed": observed, "causes": causes, "never": never}


def investigation_text(inv):
    """The ranking, as ATLAS says it."""
    lines = []
    for c in inv["causes"][:5]:
        lines.append("- **{0}** — {1}. {2} Check: {3}".format(
            c["status"].title(), c["cause"], c["why"], c["check"]))
    out = "\n".join(lines)
    if inv.get("never"):
        out += "\n\nWhat I won't do: " + "; ".join(inv["never"]) + "."
    return out
