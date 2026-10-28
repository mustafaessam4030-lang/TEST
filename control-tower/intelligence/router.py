"""
What kind of question is this? — decided in microseconds, without a model.

    route(question, context=None) -> {"route", "confidence", "signals"}

Routes:
    operational      our shipments, the run, carriers' statuses, ETAs/ATAs,
                     clearance figures: answered from the live records only
    troubleshooting  why something of ours went wrong (records + knowledge)
    atlas_rules      how this platform / ATLAS / the automation works
    action           asks ATLAS to do something (stop, rerun, email, update)
    calculation      a computation whose numbers are in the message
    knowledge        general logistics knowledge, including applying it to a
                     described situation ("we bought CFR, who pays the THC?")
    scenario         a described problem to reason about, not about a
                     record ATLAS holds ("customs held our box, invoice and
                     packing list weights differ, what now?")
    follow_up        only meaningful with the previous turn
    ambiguous        too vague to answer: ATLAS asks what is meant
    chat             small talk
    out_of_scope     not logistics, not the platform

The router only labels. Operational questions keep their rule: the records
answer them, never a definition. A label with low confidence leaves the
existing behaviour unchanged; the model, when one is set up, reads the
question only when the router cannot.
"""
import re

REF = re.compile(r"\b\d{7,12}\b|\b[A-Z]{1,4}-?\d{6,}\b|\b(?=[A-Za-z0-9]*\d{5})(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{8,}\b|"
                 r"\b\d{3}-\d{8}\b")
RUN = re.compile(r"\b(the run|this run|today'?s run|last run|in the run|run (?:finished|results?|status)|the automation|"
                 r"the hub|in the hub|the tower|the dashboard|the robot|the bot)\b|الأوتوميشن|الاوتوميشن|التشغيل", re.I)
STATUS = re.compile(r"\b(where(?:'s| is| are)|status|landed|landing|arrived yet|arrive yet|eta (?:on|of|for)|ata (?:on|of|for)|"
                    r"updated? or not|get updated|did .{0,30}(?:arrive|update|fail|land)|last status|latest status|any news|"
                    r"tracking (?:of|for)|stuck|how many (?:shipments|failed|need|were|are)|"
                    r"which (?:shipment|container|box|order|awb|flight|carrier|one)s?|the latest (?:shipment|failure)|"
                    r"latest one)\b|"
                    r"وصلت ولا|فين الشحن|حالة الشحن|كام شحن|كم شحن|الشحنة|الشحنات|شحنتنا|شحنتي|شحناتنا|الحاوية بتاعتنا|"
                    r"فشلت|اتحدثت|تحدثت|اتأخرت|تأخرت", re.I)
OURS = re.compile(r"\b(our|my|we|us)\b.{0,25}\b(shipments?|containers?|boxe?s|cargo|consignments?|orders?|awbs?|b/?ls?|"
                  r"bookings?|parts?|machines?)\b", re.I)
TIMEBOUND = re.compile(r"\b(today|tomorrow|yesterday|this week|right now|currently|so far)\b|النهارده|بكرة|بكره|امبارح", re.I)
CARRIER = re.compile(r"\b(maersk|msc|cma(?:\s*cgm)?|hapag|evergreen|cosco|one line|zim|dhl|fedex|ups|qatar|emirates|"
                     r"lufthansa|turkish|klm|ethiopian|saudia|egyptair)\b", re.I)
PLATFORM = re.compile(r"\b(atlas|the automation|automation|the hub|in the hub|the tower|control tower|dashboard|"
                      r"needs review|skipped|read-?back|human action|held carriers?|potato mode|the run results?|ehub)\b|"
                      r"الاوتوميشن|الأوتوميشن|الهب", re.I)
PLATFORM_HOW = re.compile(r"\b(what does|what do|how does|how do|what is|what's|difference between|mean|means|exactly|"
                          r"how often|when (?:does|do)|why does it|what happens)\b|بيشتغل|بيحدث|بيحدّث|يعني ايه|ايه الفرق", re.I)
ACTION = re.compile(r"^\s*(?:please\s+|pls\s+|can you\s+|could you\s+)?(stop|pause|start|resume|re-?run|retry|restart|"
                    r"send|email|mail|update|change|cancel|delete|upload|download|export|run|approve|reject)\b|"
                    r"^\s*(وقف|شغل|ابعت|ارسل|حدث|حدّث|الغي)", re.I)
WHY = re.compile(r"\b(why|what went wrong|what'?s wrong|what is going on|what'?s going on|how come|reason)\b|ليه|لماذا|ليش", re.I)
GENERAL = re.compile(
    r"\b(usually|typically|normally|in general|generally|why would|why do(?:es)? (?:people|carriers|customs|forwarders|they)|"
    r"how does .{1,40} work|how do(?:es)? .{1,30} work|when (?:is it|does it make sense|should)|is it worth|worth (?:it|consolidating)|"
    r"any catch|what'?s the catch|trade-?offs?|pros and cons|does it matter|what counts as|what should we|how do (?:we|i) (?:set|choose|decide|pick)|"
    r"how should|what'?s (?:usually )?smarter|which (?:one )?is (?:better|cheaper|smarter)|what does that (?:do|mean)|does that mean|"
    r"who(?:'s| is)? (?:pays?|paying|bears?|carr(?:y|ies)|responsible|on the hook)|what'?s on us|who arranges|"
    r"what goes into|vs\.?|versus|or .{1,30}\?|difference between|what is|what's a|what are|define|explain|meaning of)\b|"
    r"ايه الفرق|إيه الفرق|الفرق بين|يعني ايه|يعني إيه|ما هو|ما هي|مين (?:المفروض )?(?:يدفع|شايل)|مين بيدفع|ينفع|ولا لازم|ده معناه|"
    r"الترتيب الطبيعي|ليها أصل", re.I)
INCOTERM = re.compile(r"\b(EXW|FCA|CPT|CIP|DAP|DPU|DDP|FAS|FOB|CFR|CIF)\b")
CALC_SHAPE = re.compile(r"(\d[\d,.]*\s*(?:x|×|\*)\s*\d)|\b(calculate|compute|work out|how much|total\??|average\??|"
                        r"variance|in %|percent|per cent|cbm|chargeable|volumetric|turns?|mape|dwell time in|lead time in|"
                        r"days? (?:between|from)|متوسط|احسب|كام|الوزن اللي هيتحاسب)\b", re.I)
NUMBERS = re.compile(r"\d[\d,.]*")
WHAT_IS = re.compile(r"\b(what is|what's a|what are|define|explain|meaning of)\b|ما هو|ما هي", re.I)
# Not logistics, whatever place it names.
OFF_TOPIC = re.compile(r"\b(weather|rain|recipe|cook\w*|homework|fractions?|football|soccer|poem|song|movie|joke|"
                       r"horoscope|birthday)\b|وصفة|كشري|طبخ|كورة|نكتة", re.I)
PROBLEM = re.compile(r"\b(wrong|late|says|but|missing|won'?t|doesn'?t|didn'?t|billed|charged|is (?:that|it) (?:right|wrong|correct|"
                     r"actually)|should we|what now|what do we do|dispute|short|damaged|believe|trust)\b|بس|غلط|متأخر|ليه|أصدق|أصدّق|اصدق|مين الصح", re.I)
FOLLOW = re.compile(r"^\s*(and|what about|how about|and the|the other|also|و\s*|وطيب|طيب|وده|والتانية|والتاني)\b|"
                    r"\b(it|its|that one|this one|the other one|them|those|ده|دي|التانية|التاني)\b", re.I)
VAGUE = re.compile(r"^\s*(how much is it|what about it|any news|and\??|so\??|the (?:\w+ )?one,? any news|status\??|update\??)\s*\??$", re.I)
CHAT = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|good (?:morning|evening|night)|how are you|i'?m (?:so )?(?:tired|sad|bored|happy))\b|"
                  r"^\s*(مرحبا|اهلا|أهلا|شكرا|صباح الخير|مساء الخير)", re.I)
# Logistics vocabulary: its absence (with nothing else) means out of scope.
DOMAIN = re.compile(
    r"\b(ship\w*|cargo|freight|container|box|boxes|pallet|crate|carton|vessel|port|terminal|airport|flight|air|ocean|sea|truck|"
    r"rail|warehouse|stock|inventory|supplier|vendor|order|po|invoice|customs|clearance|broker|duty|duties|tax|hs|incoterms?|"
    r"bl|b/l|awb|waybill|bill of lading|pod|delivery|deliver\w*|carrier|forwarder|eta|ata|etd|atd|transit|transshipment|"
    r"demurrage|detention|free time|storage|thc|baf|surcharge|lcl|fcl|ftl|ltl|cbm|kg|weight|otif|kpi|sla|lead time|"
    r"dwell|backorder|fill rate|forecast|logistic\w*|supply chain|parts?|spares?|dangerous goods|lithium|insurance|"
    r"exw|fca|cpt|cip|dap|dpu|ddp|fas|fob|cfr|cif|roro|ro-ro|breakbulk|break-bulk|machine|equipment|loader|"
    r"excavator|sku|procurement|purchase|receiving|returns?|claim|tariff|booking|milestone|consolidat\w*)\b|"
    r"شحن|شحنة|حاوية|جمارك|الجمارك|ميناء|الميناء|بوليصة|الأرضيات|الديمرج|الديمراج|مخزن|مخزون|مورد|فاتورة|التخليص|طيران|بحري|"
    r"الحجز|التسليم|كرتونة|كيلو|الوزن|البروكر|الخط الأحمر|دولار", re.I)


# The rules' own intents (dashboard/assistant.py detect_intent), as routes.
INTENT_ROUTE = {"run_control": "action", "reprocess": "action", "download": "action", "human_resume": "action",
                "code_request": "action", "greeting": "chat", "thanks": "chat",
                "identity": "atlas_rules", "capabilities": "atlas_rules", "help": "atlas_rules", "system": "atlas_rules",
                "why": "troubleshooting", "latest_failure": "troubleshooting", "failure_cats": "troubleshooting",
                "recovery": "troubleshooting"}


def route(question, context=None, intent=None):
    """`intent`: the rules' own reading (detect_intent), when the caller has it."""
    q = " ".join(str(question or "").split())
    ctx = context or {}
    has_ref_ctx = bool(ctx.get("reference"))
    s = []

    def out(name, conf):
        return {"route": name, "confidence": conf, "signals": s}

    if not q:
        return out("ambiguous", 0.9)
    ref = REF.search(q)
    words = len(q.split())
    domain = bool(DOMAIN.search(q)) or bool(INCOTERM.search(q))
    if ref:
        s.append("reference")
    if intent:
        s.append("rules:" + intent)
    if CHAT.search(q) and words <= 6:
        return out("chat", 0.9)
    if OFF_TOPIC.search(q) and not re.search(r"\b(ship\w*|cargo|freight|container|customs|carrier|port|delivery)\b", q, re.I):
        return out("out_of_scope", 0.8)
    if ACTION.search(q) and not WHY.search(q) and \
            (RUN.search(q) or ref or re.search(r"\b(automation|run|email|mail|hub|excel|report|csv|summary)\b", q, re.I)):
        s.append("imperative")
        return out("action", 0.85)
    previous = has_ref_ctx or bool(ctx.get("previous"))
    if words <= 6 and FOLLOW.search(q) and not ref and not INCOTERM.search(q):
        s.append("pronoun")
        return out("follow_up" if previous else "ambiguous", 0.75)
    if VAGUE.search(q) and not ref:
        return out("follow_up" if previous else "ambiguous", 0.8)
    nums = NUMBERS.findall(re.sub(r"\b\d{7,12}\b", " ", q))
    if CALC_SHAPE.search(q) and len(nums) >= 2 and not ref:
        s.append("numbers")
        return out("calculation", 0.8)
    platform = PLATFORM.search(q)
    if platform and PLATFORM_HOW.search(q) and not ref and not STATUS.search(q):
        s.append("platform")
        return out("atlas_rules", 0.8)
    listing = re.search(r"^\s*(show|list|give me|display|filter)\b.{0,40}\bshipments?\b|\b(the|our|my) (?:\w+ ){0,2}shipments\b", q, re.I)
    data = ref or RUN.search(q) or STATUS.search(q) or listing or (CARRIER.search(q) and re.search(
        r"\b(box|container|shipment|booking|flight|awb|landing|going to|for alex|to alex|to cairo)\b", q, re.I)) or \
        (OURS.search(q) and TIMEBOUND.search(q)) or (TIMEBOUND.search(q) and re.search(r"\bshipments?\b", q, re.I))
    meaning = re.search(r"\b(what does that mean|does that mean|what does it mean)\b|ده معناه|يعني ايه|يعني إيه", q, re.I)
    if data and meaning and not ref and not RUN.search(q):
        data = None                       # "the broker says it went to the red channel — what does that mean?"
    if data:
        s.append("our data")
        if WHY.search(q) or (words >= 12 and PROBLEM.search(q)):
            return out("troubleshooting", 0.8)
        return out("operational", 0.85)
    general = GENERAL.search(q) or INCOTERM.search(q)
    # A bare "what is / what are" is a weak cue: a short question the rules
    # recognise as theirs ("ما هي الشحنات التي فشلت؟") stays with the rules.
    weak_general = general and not INCOTERM.search(q) and not GENERAL.search(WHAT_IS.sub(" ", q))
    if WHY.search(q) and re.search(r"\b(hub|portal|doesn'?t match|didn'?t update|half the|our run|the run)\b", q, re.I):
        return out("troubleshooting", 0.7)
    if general and domain and not (weak_general and intent and words <= 12):
        s.append("general")
        return out("knowledge", 0.75 if not weak_general else 0.65)
    if intent and words <= 12:
        # The rules recognised one of their own (short) questions about the run.
        # Their phrases fire on common words inside long described situations
        # ("count", "clearance"), so a long message is not taken on them alone.
        return out(INTENT_ROUTE.get(intent, "operational"), 0.7)
    if platform:
        return out("atlas_rules", 0.6)
    if not domain:
        return out("out_of_scope" if words >= 2 else "ambiguous", 0.6)
    s.append("domain words")
    return out("knowledge", 0.6 if (q.endswith("?") or words >= 5) else 0.5)
