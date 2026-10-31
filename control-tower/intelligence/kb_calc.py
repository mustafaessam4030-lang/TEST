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
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(" ".join(str(v).replace(",", " ").split()), fmt).date()
        except ValueError:
            continue
    raise ValueError("{0!r} is not a date (dd/mm/yyyy, yyyy-mm-dd or 12 Jan 2026)".format(v))


DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y")


def _datetime(v):
    for fmt in ("%d %b %Y %H:%M", "%d %B %Y %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(" ".join(str(v).replace(",", " ").split()), fmt)
        except ValueError:
            continue
    raise ValueError("{0!r} is not a date and time".format(v))


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


def hours_between(start, end):
    a, b = _datetime(start), _datetime(end)
    if b < a:
        raise ValueError("the end is before the start")
    h = (b - a).total_seconds() / 3600.0
    return {"kpi": "Elapsed hours", "value": round(h, 2), "unit": "hours",
            "working": "{0} → {1} = {2} hours".format(start, end, _f(h)), "inputs": {"start": start, "end": end}}


def calendar_days(start, end, what="Elapsed days"):
    n = days_between(start, end)
    return {"kpi": what, "value": n, "unit": "days", "working": "{0} → {1} = {2} calendar days (end minus start)".format(
        start, end, n), "inputs": {"start": str(start), "end": str(end)}}


def average(values, what="Average"):
    vals = [_num(v, "value") for v in values]
    if not vals:
        raise ValueError("no values to average")
    m = sum(vals) / len(vals)
    return {"kpi": what, "value": round(m, 2), "unit": "", "working": "({0}) ÷ {1} = {2}".format(
        " + ".join(_f(v) for v in vals), len(vals), _f(m)), "inputs": {"values": vals}}


def volume_cbm(length, width, height, unit="m", count=1):
    to_m = {"m": 1.0, "cm": 0.01, "mm": 0.001}[unit]
    l, w, h = (_num(v, n, positive=True) * to_m for v, n in ((length, "length"), (width, "width"), (height, "height")))
    c = _num(count, "count", positive=True)
    v = l * w * h * c
    return {"kpi": "Volume", "value": round(v, 3), "unit": "m³", "working": "{0} × {1} × {2} m{3} = {4} m³".format(
        _f(l), _f(w), _f(h), " × {0}".format(_f(c)) if c != 1 else "", _f(v)), "inputs": {"dims_m": [l, w, h], "count": c}}


def fill_rate(supplied, ordered):
    return ratio(supplied, ordered, "quantity supplied at first attempt", "quantity ordered", "Fill rate")


def inventory_turns(cogs, avg_inventory):
    c, i = _num(cogs, "cost of goods sold"), _num(avg_inventory, "average inventory", positive=True)
    return {"kpi": "Inventory turnover", "value": round(c / i, 2), "unit": "turns", "working": "{0} ÷ {1}".format(
        _f(c), _f(i)), "inputs": {"cogs": c, "average inventory": i}}


def mape(actuals, forecasts):
    a = [_num(x, "actual") for x in actuals]
    f = [_num(x, "forecast") for x in forecasts]
    if len(a) != len(f) or not a:
        raise ValueError("give the same number of actuals and forecasts ({0} and {1})".format(len(a), len(f)))
    if any(x == 0 for x in a):
        raise ValueError("MAPE is undefined when an actual is zero (period {0}); use a weighted error such as "
                         "WAPE (sum of |actual − forecast| ÷ sum of actuals) instead".format(a.index(0) + 1))
    errs = [abs(x - y) / x for x, y in zip(a, f)]
    m = 100.0 * sum(errs) / len(errs)
    return {"kpi": "MAPE", "value": round(m, 2), "unit": "%", "working": "mean of |actual − forecast| ÷ actual = ({0}) ÷ {1} × 100".format(
        " + ".join("{0:.4f}".format(e) for e in errs), len(errs)), "inputs": {"actuals": a, "forecasts": f}}


def reorder_point(daily_demand, lead_time_days, safety_stock=0):
    d, lt, ss = _num(daily_demand, "daily demand"), _num(lead_time_days, "lead time (days)"), _num(safety_stock, "safety stock")
    v = d * lt + ss
    return {"kpi": "Reorder point", "value": round(v, 2), "unit": "units", "working": "{0} × {1} + {2} = {3}".format(
        _f(d), _f(lt), _f(ss), _f(v)), "inputs": {"daily demand": d, "lead time": lt, "safety stock": ss}}


def tiered_storage(end_day, free_days, tiers):
    """Charges from day free_days+1 to end_day (day 1 = the first day counted,
    the last day counted), each day at its tier's rate. tiers: [(from_day,
    to_day or None, rate)]."""
    e, fr = _num(end_day, "last day"), _num(free_days, "free days")
    if e != int(e) or fr != int(fr):
        raise ValueError("days must be whole numbers")
    total, parts = 0.0, []
    for day in range(int(fr) + 1, int(e) + 1):
        rate = next((r for a, b, r in tiers if day >= a and (b is None or day <= b)), None)
        if rate is None:
            raise ValueError("no rate is given for day {0}".format(day))
        total += rate
    for a, b, r in tiers:
        lo, hi = max(a, int(fr) + 1), min(b if b is not None else int(e), int(e))
        if hi >= lo:
            parts.append("{0} day(s) × {1}".format(hi - lo + 1, _f(r)))
    return {"kpi": "Demurrage / storage charge", "value": round(total, 2), "unit": "currency",
            "working": "days {0}–{1} charged: {2} = {3}".format(int(fr) + 1, int(e), " + ".join(parts) or "none", _f(total)),
            "inputs": {"last day": e, "free days": fr, "tiers": tiers}}


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
    q = _normalise(" ".join(str(question or "").split()))
    low = q.lower()
    try:
        more = _more(q, low)
        if more is not None:
            return more
        if re.search(r"chargeable|volumetric|dim(ensional)? weight|الوزن اللي هيتحاسب|الوزن المحاسب", low):
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
            if dims and not kg:
                return {"missing": "the actual (gross) weight in kg — the chargeable weight is the greater of it and the volumetric weight"}
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
                out = cost_variance(_value(actual.group(1)), _value(quoted.group(1)))
                if re.search(r"%|percent|per cent|in percentage", low):
                    out = dict(out, kpi="Cost variance (%)", value=out["percent"], unit="%")
                return out
            return {"missing": "which figure is the actual cost and which the expected one"}
        if re.search(r"exception rate", low) and len(nums) >= 2:
            return _ratio(exception_rate, tl, r"exceptions?|issues?|problems?", r"handled|shipments|total",
                          "the shipments with an exception and the shipments handled")
    except ValueError as e:
        return {"error": str(e)}
    return None


AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _normalise(q):
    """Arabic digits, units and thousands; '350k'. Nothing else is rewritten."""
    q = q.translate(AR_DIGITS)
    q = re.sub(r"(\d)\s*(?:سم|سنتي)", r"\1 cm", q)
    q = re.sub(r"(\d)\s*(?:كيلو(?:جرام)?|كجم|كغ)", r"\1 kg", q)
    q = re.sub(r"(\d[\d.]*)\s*(?:ألف|الف)", lambda m: _f(float(m.group(1)) * 1000).replace(",", ""), q)
    q = re.sub(r"\b(\d+(?:\.\d+)?)k\b", lambda m: _f(float(m.group(1)) * 1000).replace(",", ""), q, flags=re.I)
    q = re.sub(r"بالقسمة على|قسمة على", " divisor ", q)
    q = re.sub(r"(\d)\s*×\s*", r"\1 x ", q)
    return q


def _list_after(low, words):
    m = re.search(r"(?:" + words + r")\s*:?\s*((?:-?\d[\d,.]*\s*(?:,|and|&|;)?\s*){2,})", low)
    if not m:
        return None
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", m.group(1).replace(",", " , "))]


def _more(q, low):
    """The calculations added in the upgrade, or None for the original parsers."""
    if re.search(r"\bmape\b|forecast (?:error|accuracy)", low):
        a, f = _list_after(low, r"actuals?"), _list_after(low, r"forecasts?")
        if a is None or f is None:
            return {"missing": "the actual and forecast values per period (e.g. 'actuals 10, 12, 8; forecasts 11, 10, 9')"}
        return mape(a, f)
    if re.search(r"inventory turns?|inventory turnover|stock turns?", low):
        c = re.search(r"cogs\D{0,20}?(\d[\d,.]*)|cost of goods sold\D{0,20}?(\d[\d,.]*)", low)
        i = re.search(r"(?:average|avg)\s+inventory\D{0,20}?(\d[\d,.]*)", low)
        if not (c and i):
            return {"missing": "the cost of goods sold and the average inventory value for the period"}
        return inventory_turns(_value(c.group(1) or c.group(2)), _value(i.group(1)))
    if re.search(r"fill rate", low):
        pw = _part_whole(low, r"supplied|shipped|filled|delivered", r"ordered|demand|requested")
        return fill_rate(*pw) if pw else {"missing": "the quantity supplied at first attempt and the quantity ordered"}
    if re.search(r"reorder point|\brop\b|re-order point", low):
        d = re.search(r"(\d[\d,.]*)\s*(?:units?|pcs|pieces)?\s*(?:a|per|/)\s*day", low)
        lt = re.search(r"lead time\D{0,15}?(\d[\d,.]*)\s*days?|(\d[\d,.]*)\s*-?\s*days? lead time", low)
        ss = re.search(r"safety stock\D{0,15}?(\d[\d,.]*)|(\d[\d,.]*)\s*(?:units? )?(?:of )?safety stock", low)
        if not (d and lt):
            return {"missing": "the average daily demand and the replenishment lead time in days (and any safety stock)"}
        return reorder_point(_value(d.group(1)), _value(lt.group(1) or lt.group(2)),
                             _value(ss.group(1) or ss.group(2)) if ss else 0)
    if re.search(r"\bcbm\b|cubic met|\bm3\b|m³|volume", low):
        dims = re.search(_NUM + r"\s*x\s*" + _NUM + r"\s*x\s*" + _NUM + r"\s*(cm|mm|m)?\b", low)
        if dims:
            cnt = re.search(r"(?<![\w.,])(\d+)\s*(?:pallets?|boxes|crates?|cartons?|pieces|pcs|packages|cases)\b", low)
            unit = dims.group(4) or ("m" if all(_value(x) < 20 for x in dims.groups()[:3]) else None)
            if unit is None:
                return {"missing": "the unit of the dimensions (m or cm)"}
            return volume_cbm(*(_value(x) for x in dims.groups()[:3]), unit=unit, count=cnt.group(1) if cnt else 1)
    if re.search(r"demurrage|detention|storage|free (?:time|days)", low):
        tiers = [(int(a), int(b), _value(r)) for a, b, r in
                 re.findall(r"days?\s*(\d+)\s*(?:-|–|to)\s*(\d+)\s*(?:at|@|:)?\s*(\d[\d,.]*)", low)]
        tiers += [(int(a), None, _value(r)) for a, r in
                  re.findall(r"days?\s*(\d+)\s*(?:onwards?|\+|and (?:after|beyond)|on)\s*(?:at|@|:)?\s*(\d[\d,.]*)", low)]
        end = re.search(r"(?:left|gated? out|out|picked up|collected|returned)\D{0,12}?day\s*(\d+)|day\s*(\d+)\s*(?:gate|out)", low)
        free = re.search(r"(\d+)\s*free\s*days?|free (?:time|days?)\D{0,6}?(\d+)", low)
        flat = re.search(r"(\d[\d,.]*)\s*(?:usd|eur|egp|\$)?\s*(?:/|per|a)\s*day|(?:\$|usd|eur)\s*(\d[\d,.]*)\s*(?:/|per|a)\s*day", low)
        if end and free and (tiers or flat):
            if not tiers:
                tiers = [(int(_value(free.group(1) or free.group(2))) + 1, None, _value(flat.group(1) or flat.group(2)))]
            return tiered_storage(_value(end.group(1) or end.group(2)), _value(free.group(1) or free.group(2)), tiers)
        if (flat or tiers) and free and not end and len(DATE.findall(q)) < 2 and not re.search(r"\d+\s*days? (?:in|at) (?:the )?(?:port|terminal)", low):
            return {"missing": "how long the container stayed — the discharge (or start) date and the gate-out date, or the day it left"}
    dts = re.findall(r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\s+\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}", q)
    if len(dts) >= 2 and re.search(r"\bhours?\b", low):
        return hours_between(dts[0], dts[1])
    dates = re.findall(r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}\b|"
                       r"\b\d{1,2}/\d{1,2}/\d{4}\b|\b\d{4}-\d{2}-\d{2}\b", q, re.I)
    if len(dates) >= 2 and re.search(r"lead time|calendar days|how many days|days between|transit (?:time|days)|cycle time|in days", low):
        what = "Lead time" if "lead time" in low else "Transit time" if "transit" in low else "Elapsed days"
        return calendar_days(dates[0], dates[1], what)
    if re.search(r"\baverage\b|\bmean\b|\bavg\b|متوسط", low) and not re.search(r"per shipment|cost per|inventory", low):
        nums = re.findall(r"(?<![\w.,])-?\d+(?:\.\d+)?(?![\w])", re.sub(r"\d+\s*(?:last|previous)", " ", _counts_text(q)))
        vals = [float(x) for x in nums]
        listed = re.search(r"\d[\d.]*\s*,\s*\d[\d.]*\s*,\s*\d", q)
        if listed and len(vals) >= 3:
            m = re.search(r":\s*((?:-?\d[\d.]*\s*,?\s*(?:and\s*)?)+)", low)
            if m:
                vals = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", m.group(1))]
            return average(vals, "Average")
    if re.search(r"شحنة|شحنات", low) and re.search(r"متوسط", low):
        nums = _scan(_counts_text(q))
        count = re.search(r"(\d[\d,]*)\s*(?:شحنة|شحنات)", low)
        if count and len(nums) == 2:
            n = _value(count.group(1))
            return cost_per_shipment(next(x for x in nums if x != n), n)
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
