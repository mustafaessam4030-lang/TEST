"""
The deterministic check between a language model and the operator.

A phrasing is accepted only when every concrete thing in it — a number, a
date, a shipment / container / declaration reference, a URL, a status label,
a success or verification word — is already in what ATLAS handed the model
(its own answer, the run evidence, the labelled web snippets), and a success
word is only used affirmatively where the inputs use it affirmatively ("not
written" never becomes "written"). Anything else is a violation, and ATLAS
shows its own answer instead. This is a text test, not
a model's opinion of itself.
"""

import re

NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
REFERENCE = re.compile(r"\b[A-Z0-9][A-Z0-9-]{5,}\b")
URL = re.compile(r"https?://\S+", re.I)
# A clock time: its two-digit parts pass as "small counts", so it is checked whole.
TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])\s*([ap]\.?m\b\.?)?", re.I)
# In the inputs a time may carry seconds (14:05:00, 2026-10-16T14:05:00).
SOURCE_TIME = re.compile(r"(?<![\d:+-])(\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?![\d:])"
                         r"\s*([ap]\.?m\b\.?)?", re.I)


def _clock(h, m, ampm):
    """(hour 0-23, minute) for a time, with AM/PM read when given."""
    h = int(h)
    if ampm:
        pm = ampm.lower().startswith("p")
        h = (h % 12) + (12 if pm else 0)
    return h, m
CLAIMS = re.compile(r"\b(verified|confirmed|succeeded|success|successful(?:ly)?|written|saved|"
                    r"updated|sent|delivered|accepted|completed|resolved|recovered|fixed|"
                    r"cleared|approved|arrived|found|downloaded|extracted|attached|"
                    r"uploaded)\b", re.I)
# The same claim in another form of its verb: inputs saying "the run wrote it
# to the Hub" affirm "written". Only the verb form changes, never the claim.
FORMS = {"wrote": "written"}
SOURCE_CLAIMS = re.compile(CLAIMS.pattern[:-3] + "|" + "|".join(FORMS) + r")\b", re.I)
# Operational status labels: a phrasing may only use the ones ATLAS used.
STATUS = re.compile(r"\b(?:[A-Z]{3,}(?:_[A-Z]+)+|SUCCESS|FAILED|RESTRICTED|VERIFIED|UNVERIFIED|"
                    r"CONFIRMED|PARTIAL|SKIPPED|BLOCKED|READY|UNKNOWN|LIKELY|POSSIBLE)\b")
NEGATION = re.compile(r"\b(not|no|never|nothing|none|without|neither|nor|cannot|can't|couldn't|"
                      r"wasn't|isn't|weren't|aren't|hasn't|haven't|hadn't|didn't|doesn't|"
                      r"don't|won't|failed to|unable to|yet to)\b", re.I)


def _negated(text, start):
    """Is the word at `start` negated in its own clause ("nothing was ... written")?"""
    before = re.split(r"[.;:!?\n]|\bbut\b", text[max(0, start - 80):start])[-1]
    return bool(NEGATION.search(" ".join(before.split()[-6:])))


def _affirmed(text):
    """The claim words `text` states affirmatively (not negated)."""
    return {FORMS.get(m.group(0).lower(), m.group(0).lower())
            for m in SOURCE_CLAIMS.finditer(text) if not _negated(text, m.start())}


def _digits(text):
    return {n.replace(",", "") for n in NUMBER.findall(text or "")}


def check(phrased, inputs):
    """(ok, violations) — `inputs` is every text the model was given."""
    source = " ".join(str(x) for x in inputs if x)
    src_low = source.lower()
    src_nums = _digits(source)
    src_flat = re.sub(r"[^A-Z0-9]", "", source.upper())
    violations = []
    for n in NUMBER.findall(phrased or ""):
        value = n.replace(",", "")
        # Small counts and list numbers ("2 checks", "1.") are not facts to verify.
        if re.fullmatch(r"\d{1,2}", value):
            continue
        if value not in src_nums and value.rstrip("0").rstrip(".") not in {
                s.rstrip("0").rstrip(".") for s in src_nums}:
            violations.append("number {0}".format(n))
    for ref in REFERENCE.findall((phrased or "").upper()):
        if re.search(r"\d", ref) and len(ref) >= 6 and re.sub(r"[^A-Z0-9]", "", ref) not in src_flat:
            violations.append("reference {0}".format(ref))
    found = SOURCE_TIME.findall(source)
    src_times = {_clock(h, m, ap) for h, m, ap in found}
    plain = {(int(h), m) for h, m, ap in found if not ap}      # "2:05" with no AM/PM
    for h, m, ap in TIME.findall(phrased or ""):
        if _clock(h, m, ap) not in src_times and (int(h), m) not in plain:
            violations.append("time {0}:{1}".format(h, m))
    for url in URL.findall(phrased or ""):
        if url.rstrip(".,);") not in source:
            violations.append("link {0}".format(url[:60]))
    src_affirmed = _affirmed(source)
    for claim in CLAIMS.finditer(phrased or ""):
        word = claim.group(0).lower()
        if word not in src_low and not any(
                re.search(r"\b{0}\b".format(form), src_low)
                for form, same in FORMS.items() if same == word):
            violations.append("claim '{0}'".format(claim.group(0)))
        elif not _negated(phrased, claim.start()) and word not in src_affirmed:
            # "nothing was written" must not come back as "it was written".
            violations.append("claim '{0}' (the inputs only say it did not happen)".format(
                claim.group(0)))
    for label in set(STATUS.findall(phrased or "")):
        if label not in source:
            violations.append("status {0}".format(label))
    return not violations, violations[:8]
