"""
Exact answers the knowledge engine computes instead of wording: the Incoterms
2020 responsibilities as a table, and the KPI and freight calculations.

Everything here is deterministic and tested. A calculation returns its
inputs, its formula and its result, so an answer can show its working; a
missing or impossible input raises ValueError with what is wrong, and nothing
is assumed in its place.
"""
import math
import re
from datetime import date, datetime

# ── Incoterms 2020 ──────────────────────────────────────────────────────────
# Who does what under each rule (ICC Incoterms 2020, summarised; the ICC text
# and the sale contract govern). "seller" / "buyer".
#   delivery      where the seller delivers (= where risk passes, except as noted)
#   carriage      who contracts and pays the main carriage
#   insurance     who must insure for the other party (None = no obligation)
#   export        who clears for export;   import  who clears for import and pays duties
#   load_origin   who loads at origin;     unload_dest  who unloads at destination
#   modes         "any" or "sea" (sea and inland waterway only)
INCOTERMS = {
    "EXW": {"name": "Ex Works", "modes": "any", "delivery": "goods placed at the buyer's disposal at the seller's premises or named place, not loaded",
            "carriage": "buyer", "insurance": None, "export": "buyer", "import": "buyer", "load_origin": "buyer", "unload_dest": "buyer"},
    "FCA": {"name": "Free Carrier", "modes": "any", "delivery": "goods handed to the buyer's carrier at the named place (loaded by the seller if that place is the seller's premises)",
            "carriage": "buyer", "insurance": None, "export": "seller", "import": "buyer", "load_origin": "seller if at its premises, otherwise buyer's carrier", "unload_dest": "buyer"},
    "CPT": {"name": "Carriage Paid To", "modes": "any", "delivery": "goods handed to the first carrier (risk passes there although the seller pays carriage to destination)",
            "carriage": "seller", "insurance": None, "export": "seller", "import": "buyer", "load_origin": "seller", "unload_dest": "buyer unless the carriage contract includes it"},
    "CIP": {"name": "Carriage and Insurance Paid To", "modes": "any", "delivery": "goods handed to the first carrier (risk passes there although the seller pays carriage and insurance to destination)",
            "carriage": "seller", "insurance": "seller — minimum Institute Cargo Clauses (A), all risks", "export": "seller", "import": "buyer", "load_origin": "seller", "unload_dest": "buyer unless the carriage contract includes it"},
    "DAP": {"name": "Delivered at Place", "modes": "any", "delivery": "goods at the named destination on the arriving means of transport, ready for unloading",
            "carriage": "seller", "insurance": None, "export": "seller", "import": "buyer", "load_origin": "seller", "unload_dest": "buyer"},
    "DPU": {"name": "Delivered at Place Unloaded", "modes": "any", "delivery": "goods unloaded at the named destination",
            "carriage": "seller", "insurance": None, "export": "seller", "import": "buyer", "load_origin": "seller", "unload_dest": "seller"},
    "DDP": {"name": "Delivered Duty Paid", "modes": "any", "delivery": "goods at the named destination, cleared for import and duties paid, ready for unloading",
            "carriage": "seller", "insurance": None, "export": "seller", "import": "seller", "load_origin": "seller", "unload_dest": "buyer"},
    "FAS": {"name": "Free Alongside Ship", "modes": "sea", "delivery": "goods alongside the buyer's vessel at the named port of shipment",
            "carriage": "buyer", "insurance": None, "export": "seller", "import": "buyer", "load_origin": "buyer", "unload_dest": "buyer"},
    "FOB": {"name": "Free On Board", "modes": "sea", "delivery": "goods on board the buyer's vessel at the named port of shipment",
            "carriage": "buyer", "insurance": None, "export": "seller", "import": "buyer", "load_origin": "seller", "unload_dest": "buyer"},
    "CFR": {"name": "Cost and Freight", "modes": "sea", "delivery": "goods on board the vessel at the port of shipment (risk passes there although the seller pays freight to the destination port)",
            "carriage": "seller", "insurance": None, "export": "seller", "import": "buyer", "load_origin": "seller", "unload_dest": "buyer unless the carriage contract includes it"},
    "CIF": {"name": "Cost, Insurance and Freight", "modes": "sea", "delivery": "goods on board the vessel at the port of shipment (risk passes there although the seller pays freight and insurance to the destination port)",
            "carriage": "seller", "insurance": "seller — minimum Institute Cargo Clauses (C)", "export": "seller", "import": "buyer", "load_origin": "seller", "unload_dest": "buyer unless the carriage contract includes it"},
}
RULE = re.compile(r"\b(" + "|".join(INCOTERMS) + r")\b", re.I)
# Where a C rule's two critical points differ: costs to destination, risk at shipment.
TWO_POINTS = ("CPT", "CIP", "CFR", "CIF")


def incoterm(rule):
    r = str(rule or "").strip().upper()
    if r not in INCOTERMS:
        raise ValueError("{0!r} is not an Incoterms 2020 rule".format(rule))
    return dict(INCOTERMS[r], rule=r)


def risk_during_main_carriage(rule):
    """Who bears loss or damage during the main international carriage."""
    r = incoterm(rule)["rule"]
    return "seller" if r in ("DAP", "DPU", "DDP") else "buyer"


def incoterm_question(question):
    """For a question that names a rule and asks who does something, the
    answer from the table: {"rule", "aspect", "party", "text"} or None."""
    m = RULE.search(question or "")
    if not m:
        return None
    # "CIF value", "FOB value": a customs valuation basis, not the trade term.
    if re.match(r"\s+(value|basis|price)\b", (question or "")[m.end():], re.I) and \
            not re.search(r"\bunder\s+" + m.group(1) + r"\b|incoterm", question or "", re.I):
        return None
    rule = m.group(1).upper()
    t = incoterm(rule)
    q = question.lower()
    asks = [
        ("modes", r"\b(can|could|may|should)\b[^?]*\b(use|used)\b|\bsuitable\b|\bwhich mode|\bfor (air|road|rail|containers?)\b|\bby air\b"),
        ("insurance", r"insur"),
        ("import", r"import\s+clear|import\s+dut|dut(y|ies)|import\s+tax|customs\s+at\s+destination|clear(s|ing)?\s+import"),
        ("export", r"export\s+clear|export\s+customs|clear(s|ing)?\s+(?:for\s+)?export"),
        ("risk", r"\brisk\b|damage|loss|lost|damaged|who\s+bears"),
        ("carriage", r"freight|carriage|book(s|ing)?\s+(the\s+)?(ship|vessel|flight|transport)|main\s+transport|pays?\s+(for\s+)?(the\s+)?(shipping|transport)"),
        ("unload_dest", r"unload"),
        ("load_origin", r"\bload(s|ing)?\b"),
        ("delivery", r"deliver|where\s+does\s+the\s+seller"),
    ]
    for aspect, pattern in asks:
        if re.search(pattern, q):
            break
    else:
        aspect = None
    if aspect is None:
        return {"rule": rule, "aspect": None, "party": None, "text": None}
    if aspect == "risk":
        during = risk_during_main_carriage(rule)
        text = ("Under {0} risk passes when the seller delivers: {1}. Loss or damage during the main carriage is the {2}'s risk."
                .format(rule, t["delivery"], during))
        return {"rule": rule, "aspect": aspect, "party": during, "text": text}
    if aspect == "modes":
        text = ("{0} can be used for any mode of transport.".format(rule) if t["modes"] == "any" else
                "{0} is for sea and inland waterway transport only; for air, road or containerised cargo handed over at a terminal, FCA (or CPT/CIP) fits.".format(rule))
        return {"rule": rule, "aspect": aspect, "party": None, "text": text}
    value = t[aspect]
    label = {"insurance": "insuring the goods", "import": "import clearance and duties", "export": "export clearance",
             "carriage": "contracting and paying the main carriage", "unload_dest": "unloading at destination",
             "load_origin": "loading at origin", "delivery": "delivery"}[aspect]
    if aspect == "delivery":
        text = "Under {0} the seller delivers when: {1}.".format(rule, value)
    elif aspect == "insurance" and value is None:
        text = ("Under {0} neither party has an obligation to insure for the other; each insures its own risk if it wants to "
                "(the buyer bears the risk from delivery).".format(rule))
    else:
        text = "Under {0}, {1}: {2}.".format(rule, label, value)
    if rule in TWO_POINTS and aspect in ("carriage", "insurance"):
        text += " Note: the seller pays to destination, but risk passed to the buyer at shipment."
    return {"rule": rule, "aspect": aspect, "party": value, "text": text}


# ── KPIs ────────────────────────────────────────────────────────────────────

def _f(x):
    """A number as written for a person: exact to 2 decimals, thousands separated, no exponent."""
    text = "{0:,.2f}".format(x)
    return text.rstrip("0").rstrip(".") if "." in text else text


def _num(v, name, positive=False):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError("{0} must be a number, got {1!r}".format(name, v))
    if not math.isfinite(x):
        raise ValueError("{0} must be a finite number, got {1!r}".format(name, v))
    if x < 0 or (positive and x == 0):
        raise ValueError("{0} must be {1}, got {2}".format(name, "above zero" if positive else "zero or more", v))
    return x


def ratio(part, whole, part_name, whole_name, kpi):
    p, w = _num(part, part_name), _num(whole, whole_name, positive=True)
    if p > w:
        raise ValueError("{0} ({1}) cannot exceed {2} ({3})".format(part_name, _f(p), whole_name, _f(w)))
    return {"kpi": kpi, "value": round(100.0 * p / w, 2), "unit": "%",
            "working": "{0} ÷ {1} × 100".format(_f(p), _f(w)), "inputs": {part_name: p, whole_name: w}}


def on_time_delivery(on_time, delivered):
    return ratio(on_time, delivered, "on-time deliveries", "deliveries", "On-time delivery")


def otif(on_time_in_full, due):
    return ratio(on_time_in_full, due, "orders on time and in full", "orders due", "OTIF")


def exception_rate(with_exception, handled):
    return ratio(with_exception, handled, "shipments with an exception", "shipments handled", "Exception rate")


def past_due_share(past_due, open_):
    return ratio(past_due, open_, "past-due open shipments", "open shipments", "Past-due share")


def cost_per_shipment(total_cost, shipments):
    c, n = _num(total_cost, "total cost"), _num(shipments, "shipments", positive=True)
    return {"kpi": "Cost per shipment", "value": round(c / n, 2), "unit": "per shipment",
            "working": "{0} ÷ {1}".format(_f(c), _f(n)), "inputs": {"total cost": c, "shipments": n}}


def cost_variance(actual, expected):
    a, e = _num(actual, "actual cost"), _num(expected, "expected cost", positive=True)
    return {"kpi": "Cost variance", "value": round(a - e, 2), "percent": round(100.0 * (a - e) / e, 2),
            "unit": "currency", "working": "{0} − {1} = {2} ({3:+.2f}%)".format(_f(a), _f(e), _f(a - e), 100.0 * (a - e) / e),
            "inputs": {"actual": a, "expected": e}}


def chargeable_weight(actual_kg, length_cm, width_cm, height_cm, divisor=6000, pieces=1):
    """Air: the greater of actual and volumetric weight; volumetric kg =
    L × W × H (cm) × pieces ÷ divisor (6000 is the common air convention;
    the carrier's contract decides)."""
    a = _num(actual_kg, "actual weight (kg)")
    l, w, h = (_num(v, n, positive=True) for v, n in ((length_cm, "length"), (width_cm, "width"), (height_cm, "height")))
    d, p = _num(divisor, "divisor", positive=True), _num(pieces, "pieces", positive=True)
    vol = l * w * h * p / d
    return {"kpi": "Chargeable weight", "value": round(max(a, vol), 2), "unit": "kg", "volumetric": round(vol, 2),
            "working": "volumetric = {0} × {1} × {2}{3} ÷ {4} = {5:.2f} kg; actual {6} kg; the greater is charged"
                       .format(_f(l), _f(w), _f(h), " × {0}".format(_f(p)) if p != 1 else "", _f(d), vol, _f(a)),
            "inputs": {"actual_kg": a, "dims_cm": [l, w, h], "pieces": p, "divisor": d}}


def _date(v):
    if isinstance(v, date):
        return v
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError("{0!r} is not a date (dd/mm/yyyy or yyyy-mm-dd)".format(v))


def days_between(start, end):
    a, b = _date(start), _date(end)
    if b < a:
        raise ValueError("the end date is before the start date")
    return (b - a).days


def chargeable_days(start, end, free_days, inclusive=True):
    """Days charged after free time (demurrage, detention or storage), counting
    calendar days from `start` (e.g. discharge) to `end` (e.g. gate-out).
    `inclusive` counts both the first and the last day, the common tariff
    convention; the carrier's or terminal's tariff decides."""
    n = days_between(start, end) + (1 if inclusive else 0)
    f = _num(free_days, "free days")
    if f != int(f):
        raise ValueError("free days must be a whole number, got {0}".format(_f(f)))
    charged = max(0, int(n - f))
    return {"kpi": "Chargeable days", "value": charged, "unit": "days", "days_used": n,
            "working": "{0} day(s) from {1} to {2}{3}, minus {4} free = {5}".format(
                n, start, end, " (both days counted)" if inclusive else "", _f(f), charged),
            "inputs": {"start": str(start), "end": str(end), "free_days": f, "inclusive": inclusive}}


def dd_kind(location):
    """Demurrage or detention, from where the (full or empty) container is."""
    loc = str(location or "").lower()
    # Outside the port first: "our yard" and "outside the terminal" name a yard
    # and a terminal but are detention.
    if re.search(r"\b(our (yard|warehouse|site|premises)|outside the (port|terminal)|consignee|customer|"
                 r"factory|trucked out|gated out)\b", loc):
        return "detention"
    if re.search(r"\b(port|terminal|yard|quay|inside|discharged|stack)\b", loc):
        return "demurrage"
    if re.search(r"\b(warehouse|premises|site|outside|consignee|customer|our yard|factory|trucked out|gated out)\b", loc):
        return "detention"
    return None


def transit_days(start, end):
    return {"kpi": "Transit time", "value": days_between(start, end), "unit": "days",
            "working": "{0} → {1}".format(start, end), "inputs": {"start": str(start), "end": str(end)}}


# ── reading a calculation out of a question ──────────────────────────────────

# One way to read a number: 1234, 1,234, 1,234,567.89, -5. "12,5" is
# ambiguous (a decimal comma, or a typo) and is refused, never guessed.
_NUM = r"(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
NUM = re.compile(r"(?<![\w.,-])" + _NUM + r"(?![\d]|[.,]\d)|(?<=[\s:(])" + _NUM + r"(?![\d]|[.,]\d)")
AMBIGUOUS = re.compile(r"(?<![\d,.])\d+,\d{1,2}(?![\d,])|\d,\d{4,}")
DATE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})\b")
# Numbers that are not counts: periods, years, references.
NOT_COUNTS = re.compile(
    r"\b[QH][1-4]\b(?:\s*(?:19|20)\d{2})?|\bFY\s*\d{2,4}\b|"
    r"\b(?:in|during|since|year|for|until|before|after|jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|"
    r"sep\w*|oct\w*|nov\w*|dec\w*)\s+(?:19|20)\d{2}\b(?!\s*(?:orders?|shipments?|deliver\w*|units?|kg|pieces|containers?))|"
    r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{6,}\b|\b\d{3}-\d{8}\b", re.I)


def _value(text):
    return float(text.replace(",", ""))


def _scan(text):
    """The numbers in a text, in order. Raises ValueError on an ambiguous one."""
    bad = AMBIGUOUS.search(text)
    if bad:
        raise ValueError("{0!r} is ambiguous — write thousands as 1,234 and decimals with a point".format(bad.group(0)))
    return [_value(m.group(1) or m.group(2)) for m in NUM.finditer(text)]


def _counts_text(q):
    """The question without dates, periods, years and references."""
    return NOT_COUNTS.sub(" ", DATE.sub(" ", q))


def _labelled(low, words):
    """The number written right before one of `words` ("34 on time"), else
    right after it ("on time: 34"). None when neither."""
    before = re.search(r"(?<![\w.,])" + _NUM + r"\s+(?:[a-z]+\s+){0,2}?(?:" + words + r")\b", low)
    if before:
        return _value(before.group(1)), before.start(1)
    after = re.search(r"\b(?:" + words + r")\b[^\d\n]{0,12}?(?<![\w.,])" + _NUM + r"(?![\d]|[.,]\d)", low)
    if after:
        return _value(after.group(1)), after.start(1)
    return None


def _part_whole(low, part_words, whole_words):
    """(part, whole) as the question states them — "X of Y", "X/Y", or the
    words next to each number — or None. Never by size, never by order."""
    m = re.search(r"(?<![\w.,])" + _NUM + r"\s*/\s*" + _NUM + r"(?![\d]|[.,]\d)", low) or \
        re.search(r"(?<![\w.,])" + _NUM + r"\b[^\d\n]{0,40}?\b(?:out of|of)\s+" + _NUM + r"(?![\d]|[.,]\d)", low)
    if m:
        return _value(m.group(1)), _value(m.group(2))
    part, whole = _labelled(low, part_words), _labelled(low, whole_words)
    if part and whole and part[1] != whole[1]:
        return part[0], whole[0]
    return None


def _ratio(fn, low, part_words, whole_words, need):
    pw = _part_whole(low, part_words, whole_words)
    return fn(*pw) if pw else {"missing": need}


def calculate(question):
    """A calculation the question asks for with its numbers given, or None.
    Never fills in a missing number, never reads a year, quarter, date or
    reference as a count, never guesses which number is which."""
    q = " ".join(str(question or "").split())
    low = q.lower()
    try:
        if re.search(r"chargeable|volumetric|dim(ensional)? weight", low):
            dims = re.search(_NUM + r"\s*(?:x|×|\*)\s*" + _NUM + r"\s*(?:x|×|\*)\s*" + _NUM +
                             r"\s*(cm|mm|m|in|inch(?:es)?|ft)?\b", low)
            kg = re.search(r"(?<![\w.,])" + _NUM + r"\s*kgs?\b", low)
            asks = re.search(r"calculat|comput|work out|how much|what is|احسب", low)
            if dims and kg:
                _scan(low)
                unit = dims.group(4)
                if unit is None:
                    return {"missing": "the unit of the dimensions (cm, mm or m)"}
                if unit not in ("cm", "mm", "m"):
                    raise ValueError("dimensions in {0}: give them in cm, mm or m (the calculator does not "
                                     "assume an imperial divisor)".format(unit))
                to_cm = {"cm": 1.0, "mm": 0.1, "m": 100.0}[unit]
                l, w, h = (_value(x) * to_cm for x in dims.groups()[:3])
                div = re.search(r"(?:divisor|÷)\s*(\d{4})", low)
                pcs = re.search(r"(?<![\w.,])(\d+)\s*(?:pieces|pcs|boxes|crates|cartons)", low)
                return chargeable_weight(_value(kg.group(1)), l, w, h, divisor=div.group(1) if div else 6000,
                                         pieces=pcs.group(1) if pcs else 1)
            if re.search(r"\b\d[\d.,]*\s*(lbs?|pounds?)\b", low):
                raise ValueError("a weight in pounds: give it in kg (the calculator does not assume a conversion)")
            if asks and (dims or kg):
                return {"missing": "the actual weight in kg and the dimensions L × W × H with their unit"}
            return None
        dates = DATE.findall(q)
        if re.search(r"demurrage|detention|storage|free (time|days)", low) and len(dates) >= 2:
            rest = DATE.sub(" ", low)
            _scan(rest)
            free = re.search(r"(?<![\w.,])" + _NUM + r"\s*(?:free\s*days?|days?\s*free|days? of free time)|"
                             r"free\s*(?:time|days?)\s*(?:of|:|=|is)?\s*" + _NUM + r"(?![\d]|[.,]\d)", rest)
            if not free:
                return {"missing": "the number of free days"}
            return chargeable_days(dates[0], dates[1], _value(free.group(1) or free.group(2)))
        text = _counts_text(q)
        tl = text.lower()
        nums = _scan(text)
        asks_calc = bool(re.search(r"calculat|comput|work out|how much|what (is|was|would be) (the|our|my)\b.*\b(rate|percent|%)|احسب", low))
        if len(nums) < 2 and not asks_calc:
            return None                                  # a definition question, not a calculation
        if re.search(r"\botif\b|on[- ]time in[- ]full", low):
            return _ratio(otif, tl, r"on[- ]time (?:and|&) in[- ]full|in[- ]full|otif",
                          r"orders? due|due|orders|total", "the orders on time and in full, and the orders due")
        if re.search(r"on[- ]time|\botd\b|punctual", low) and re.search(r"calculat|rate|percent|%|what is|compute", low):
            return _ratio(on_time_delivery, tl, r"on[- ]time|punctual", r"delivered|deliveries|total|shipments",
                          "the on-time deliveries and the total deliveries")
        if re.search(r"cost per shipment|per shipment", low) and len(nums) >= 2:
            count = _labelled(tl, r"shipments")
            costs = [x for x in nums if count is None or x != count[0]]
            if count is None or len(costs) != 1:
                return {"missing": "which number is the total cost and which the shipment count"}
            return cost_per_shipment(costs[0], count[0])
        if re.search(r"variance|over budget|vs quote|versus quote|quoted", low) and len(nums) >= 2:
            quoted = re.search(r"(?:quote[d]?|expected|budget(?:ed)?)\D{0,20}?(?<![\w.,])" + _NUM, tl)
            actual = re.search(r"(?:invoiced?|actual|paid|billed)\D{0,20}?(?<![\w.,])" + _NUM, tl)
            if quoted and actual:
                return cost_variance(_value(actual.group(1)), _value(quoted.group(1)))
            return {"missing": "which figure is the actual cost and which the expected one"}
        if re.search(r"exception rate", low) and len(nums) >= 2:
            return _ratio(exception_rate, tl, r"exceptions?|issues?|problems?", r"handled|shipments|total",
                          "the shipments with an exception and the shipments handled")
    except ValueError as e:
        return {"error": str(e)}
    return None


def describe(result):
    """One line for a calculation result."""
    if not result:
        return None
    if result.get("missing"):
        return "To calculate that I need {0}; I won't assume a value.".format(result["missing"])
    if result.get("error"):
        return "I can't calculate that: {0}.".format(result["error"])
    unit = result.get("unit")
    value = result["value"]
    shown = "{0}%".format(_f(value)) if unit == "%" else _f(value) if unit == "currency" else \
        "{0} {1}".format(_f(value), unit)
    extra = " ({0:+.2f}%)".format(result["percent"]) if "percent" in result else ""
    return "**{0} = {1}{2}** — {3}.".format(result["kpi"], shown, extra, result["working"])
