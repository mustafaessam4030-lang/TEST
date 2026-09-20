#!/usr/bin/env python3
"""Build mantrac-support-v9.html = v8 + the equipment data layer.

Additive only: nine insertion points, no existing logic rewritten. Re-runnable
against a new v8 — if any anchor moves, the build fails loudly instead of
producing a half-patched file.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "mantrac-support-v8.html"
OUT = HERE / "mantrac-support-v9.html"
MODULE = (HERE / "maya-equipment.js").read_text()

CSS = """
/* ── EQUIPMENT CARD (v9) ── */
.eq-card{border:1px solid var(--border);background:var(--white);margin-top:10px;font-size:13px;overflow:hidden;}
.eq-card.fail{border-color:#F3C6C6;}
.eq-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;padding:12px 14px;background:var(--nav);color:var(--white);}
.eq-serial{font-weight:800;font-size:15px;letter-spacing:.02em;}
.eq-sub{font-size:11px;color:rgba(255,255,255,.65);margin-top:2px;}
.eq-badges{display:flex;flex-wrap:wrap;gap:4px;justify-content:flex-end;}
.eq-badge{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:3px 7px;border-radius:2px;background:rgba(255,255,255,.14);color:var(--white);white-space:nowrap;}
.eq-badge.ok{background:var(--green);}
.eq-badge.warn{background:#B26A00;}
.eq-badge.err{background:var(--red);}
.eq-badge.live{background:var(--y);color:var(--k);}
.eq-badge.store{background:var(--blue-l);}
.eq-body{padding:10px 14px;}
.eq-row{display:flex;justify-content:space-between;gap:12px;padding:5px 0;border-bottom:1px dashed #EEE;}
.eq-row:last-child{border-bottom:none;}
.eq-k{color:var(--mid);font-size:12px;}
.eq-v{font-weight:700;color:var(--dark);text-align:right;word-break:break-word;}
.eq-v.missing{font-weight:400;font-style:italic;color:var(--light);}
.eq-specs-h{margin:10px 0 4px;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--y);}
.eq-spec{display:flex;justify-content:space-between;gap:12px;padding:3px 0;font-size:12px;color:var(--mid);}
.eq-spec b{color:var(--dark);}
.eq-warn{margin-top:8px;background:#FFF6E0;border-left:3px solid var(--y);padding:7px 10px;font-size:12px;color:#7A5B00;}
.eq-note{margin-top:8px;font-size:11.5px;color:var(--light);line-height:1.5;}
.eq-fallback{margin-top:10px;border:1px dashed var(--border);padding:9px 11px;background:#FAFAFA;font-size:12px;}
.eq-links{margin-top:10px;display:flex;flex-wrap:wrap;gap:12px;}
.eq-link{font-size:12px;font-weight:600;color:var(--blue-l);text-decoration:none;}
.eq-link:hover{text-decoration:underline;}
.eq-foot{display:flex;flex-wrap:wrap;gap:4px 16px;padding:9px 14px;background:#FAFAFA;border-top:1px solid var(--border);font-size:11px;color:var(--mid);}
.eq-foot b{color:var(--dark);font-weight:700;}
.eq-actions{display:flex;gap:8px;padding:10px 14px;border-top:1px solid var(--border);flex-wrap:wrap;}
.eq-btn{background:var(--y);color:var(--k);border:none;font-family:var(--body);font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;padding:8px 14px;cursor:pointer;transition:background .2s;}
.eq-btn:hover{background:var(--yd);}
.eq-btn.ghost{background:none;border:1px solid var(--border);color:var(--mid);}
.eq-btn.ghost:hover{background:#F5F5F5;color:var(--dark);}
@media(max-width:520px){.eq-head{flex-direction:column;}.eq-badges{justify-content:flex-start;}}
"""

CFG_BLOCK = """  showTrace: true,           // "show reasoning" panel — great for demos

  // ── EQUIPMENT DATA LAYER (v9) ───────────────────────────────────────────
  // Maia never drives a browser herself. These are deterministic backend
  // tools: internal store first, Caterpillar SIS automation only on a miss.
  // Leave apiBase empty and the lookup reports "not configured" — it will
  // never invent equipment data to fill the gap.
  equipment: {
    enabled      : true,
    apiBase      : '',                 // e.g. 'https://maya-api.mantrac.internal'
    actor        : 'maia-web',
    authToken    : '',                 // short-lived token minted by your BFF
    timeoutMs    : 45000,              // SIS cold lookups run 15-40s
    pollMs       : 4000,               // poll interval for a 202 run
    maxPollMs    : 60000,
    clientCacheMs: 120000              // de-dupe repeats inside one conversation
  }
};"""

EQUIP_INTENT = """  {id:'photo',           w:{strong:['send a photo','send a picture','upload an image','ابعت صورة','ارسل صورة'],med:['photo','picture','image','camera','صورة','كاميرا'],weak:[]}},
  {id:'equipment_lookup',w:{strong:['serial number','machine data','equipment data','equipment details','look up the serial','pin number','بيانات المعدة','داتا المعدة','رقم المعدة','السيريال','سيريال المعدة','دوريلي على المعدة'],
                            med:['serial','sn','pin','lookup','look up','sis','build date','model year','specifications','spec sheet','دوريلي','دور على','بيانات','مواصفات','تاريخ التصنيع'],
                            weak:['machine','equipment','unit','معدة','ماكينة']}}
];"""

ANCHORS: list[tuple[str, str, str]] = [
    # 1. card styles
    ("css", "</style>", CSS + "</style>"),

    # 2. runtime config for the tool layer
    ("cfg", """  showTrace: true            // "show reasoning" panel — great for demos
};""", CFG_BLOCK),

    # 3. serial/PIN recognition (additive: the old pattern still wins first)
    ("regex", """  serial : /\\b([A-Z]{3}\\d{5})\\b/i,""",
     """  serial : /\\b([A-Z]{3}\\d{5})\\b/i,
  // v9: Cat 17-char PIN, and an explicitly labelled serial in either language.
  catPin : /\\b([A-Z]{3}[A-Z0-9]{14})\\b/i,
  catSn  : /(?:s\\/?n|serial|pin|سيريال|السيريال|رقم\\s*المعدة)\\s*[:#]?\\s*([A-Za-z0-9][A-Za-z0-9-]{2,16})/i,"""),

    ("extract", """  const s=t.match(RE.serial); if(s&&!e.parts.length)e.serial=s[1].toUpperCase();""",
     """  const s=t.match(RE.serial); if(s&&!e.parts.length)e.serial=s[1].toUpperCase();
  // v9 — additive: a 17-char PIN, or a serial the user labelled explicitly.
  if(!e.serial){const pin=t.match(RE.catPin); if(pin)e.serial=pin[1].toUpperCase();}
  const tagged=t.match(RE.catSn);
  if(tagged){
    const c=tagged[1].toUpperCase().replace(/-/g,'');
    if(/^[A-Z0-9]{3,17}$/.test(c)&&!/^\\d{7}$/.test(c))e.serial=c;   // not a bare part number
  }"""),

    # 4. intent
    ("intent", """  {id:'photo',           w:{strong:['send a photo','send a picture','upload an image','ابعت صورة','ارسل صورة'],med:['photo','picture','image','camera','صورة','كاميرا'],weak:[]}}
];""", EQUIP_INTENT),

    # 5. planner marks the intent; the tool call itself happens in dispatch
    ("plan", """function plan(raw,ents,cls){
  const p={intent:cls.intent,confidence:cls.confidence,actions:[],needSlot:null,escalate:false};
""", """function plan(raw,ents,cls){
  const p={intent:cls.intent,confidence:cls.confidence,actions:[],needSlot:null,escalate:false};

  // v9 — a serial plus a data request is a tool pull, not a chat turn.
  // The call runs in dispatch(); the planner only fixes the intent.
  if(ents.serial&&(cls.intent==='equipment_lookup'||EQUIP.wantsLookup(raw))){
    p.intent='equipment_lookup';
    p.confidence=Math.max(p.confidence,0.93);
  }
"""),

    # 6. the module itself
    ("module", "// ═══════════════ MAIN DISPATCH ═══════════════",
     MODULE + "\n// ═══════════════ MAIN DISPATCH ═══════════════"),

    # 7. grounded lookup before the model runs
    ("dispatch-lookup", """  $('typingIndicator')?.classList.add('show');
  const typId=addTyping();
""", """  $('typingIndicator')?.classList.add('show');
  const typId=addTyping();

  // ── v9: run the equipment tools BEFORE the model, so the model is grounded
  // in a real record instead of reasoning about a serial from memory.
  const equip=photoB64?null:await EQUIP.maybeLookup(raw,ents,cls,L);
  if(equip)EQUIP.injectDocs(equip,docs);
"""),

    # 8. attribution + card + trace
    ("dispatch-merge", """  // Planner actions run first (deterministic), model actions layer on top""",
     """  // v9: bind the answer to the record (or to the failure) before rendering.
  if(equip)out=EQUIP.mergeAnswer(out,equip,L);

  // Planner actions run first (deterministic), model actions layer on top"""),

    ("dispatch-action", """  removeEl(typId);
  $('typingIndicator')?.classList.remove('show');
""", """  if(equip)actions.push({type:'equipment_card',token:equip.token});

  removeEl(typId);
  $('typingIndicator')?.classList.remove('show');
"""),

    ("dispatch-trace", """    docs,actions:actions.map(a=>a.type),sources:out.sources||[],usage,error:err
  };""", """    docs,actions:actions.map(a=>a.type),sources:out.sources||[],usage,error:err,
    equipment:equip?EQUIP.traceOf(equip):null
  };"""),

    # 9. renderers
    ("execute", """      case 'add_to_quote':""",
     """      case 'equipment_card': html+=EQUIP.cardFor(a.token,L);break;
      case 'add_to_quote':"""),

    ("trace-panel", """      <div class="tr-row" style="margin-top:6px;"><span class="tr-k">retrieved</span>""",
     """      ${t.equipment?EQUIP.traceRows(t.equipment,row):''}
      <div class="tr-row" style="margin-top:6px;"><span class="tr-k">retrieved</span>"""),

    # 10. the model must be told about the record it was given
    ("prompt", """RETRIEVED FACTS
${docs.length?""", """${EQUIP.promptBlock()}

RETRIEVED FACTS
${docs.length?"""),
]


def main() -> int:
    html = SRC.read_text()
    for name, anchor, replacement in ANCHORS:
        count = html.count(anchor)
        if count != 1:
            print(f"FAIL [{name}]: anchor found {count} times, expected exactly 1")
            return 1
        html = html.replace(anchor, replacement, 1)
    html = html.replace("<title>Mantrac — Customer Support · Maia AI</title>",
                        "<title>Mantrac — Customer Support · Maia AI (v9)</title>", 1)
    OUT.write_text(html)
    print(f"OK  wrote {OUT.name}  ({len(html):,} bytes, +{len(html) - len(SRC.read_text()):,})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
