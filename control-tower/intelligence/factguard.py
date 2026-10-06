"""
The deterministic check between a language model and the operator.

A phrasing is accepted only when every concrete thing in it — a number, a
date, a shipment / container / declaration reference, a URL, a success or
verification word — is already in what ATLAS handed the model (its own
answer, the run evidence, the labelled web snippets). Anything else is a
violation, and ATLAS shows its own answer instead. This is a text test, not
a model's opinion of itself.
"""

import re

NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
REFERENCE = re.compile(r"\b[A-Z0-9][A-Z0-9-]{5,}\b")
URL = re.compile(r"https?://\S+", re.I)
CLAIMS = re.compile(r"\b(verified|confirmed|succeeded|successful(ly)?|written to the hub|"
                    r"was sent|has been sent|delivered|completed|resolved|recovered|fixed)\b",
                    re.I)


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
    for claim in CLAIMS.finditer(phrased or ""):
        if claim.group(0).lower() not in src_low:
            violations.append("claim '{0}'".format(claim.group(0)))
    return not violations, violations[:8]
