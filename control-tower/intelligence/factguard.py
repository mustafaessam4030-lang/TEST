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
CLAIMS = re.compile(r"\b(verified|confirmed|succeeded|success|successful(?:ly)?|written|saved|"
                    r"updated|sent|delivered|accepted|completed|resolved|recovered|fixed|"
                    r"cleared|approved|arrived)\b", re.I)
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
    return {m.group(0).lower() for m in CLAIMS.finditer(text) if not _negated(text, m.start())}


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
    for url in URL.findall(phrased or ""):
        if url.rstrip(".,);") not in source:
            violations.append("link {0}".format(url[:60]))
    src_affirmed = _affirmed(source)
    for claim in CLAIMS.finditer(phrased or ""):
        word = claim.group(0).lower()
        if word not in src_low:
            violations.append("claim '{0}'".format(claim.group(0)))
        elif not _negated(phrased, claim.start()) and word not in src_affirmed:
            # "nothing was written" must not come back as "it was written".
            violations.append("claim '{0}' (the inputs only say it did not happen)".format(
                claim.group(0)))
    for label in set(STATUS.findall(phrased or "")):
        if label not in source:
            violations.append("status {0}".format(label))
    return not violations, violations[:8]
