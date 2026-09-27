"""Turns analysis results into a report a business user can read.

Plain text with light markdown (bold, bullets) — the chat renders it; a
terminal prints it as is. Wording describes the data, never the machine's
condition. No raw JSON unless asked for.
"""
from __future__ import annotations

import json
from typing import Any

RULE = "─" * 34


def _v(x: Any) -> Any:
    return x.get("value") if isinstance(x, dict) and "classification" in x else x


def _fmt_date(iso: str | None) -> str:
    return (iso or "—")[:16].replace("T", " ")


def _bullets(items: list[str], empty: str = "None found.") -> list[str]:
    return [f"• {i}" for i in items] if items else [f"• {empty}"]


def _freshness_line(eq: dict[str, Any]) -> str:
    fr = eq["freshness"]
    if fr.get("value") is None:
        return "retrieval time not recorded"
    days = fr["value"]
    age = f"{days:g} day(s)" if days >= 1 else f"{round(days * 24, 1):g} hour(s)"
    return f"{fr['status'].title()} — retrieved {age} ago ({_fmt_date(fr.get('retrieved_at'))})"


def _quality_line(q: dict[str, Any]) -> str:
    return q["message"] if q["score_pct"] is None else f"{q['score_pct']:g}%"


def _group_lines(parts: dict[str, Any], limit: int = 8) -> list[str]:
    per = parts["records_per_group"]["value"]
    total = parts["total_records"]["value"] or 1
    ranked = sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))
    lines = [f"• {g}: {n} record(s) ({round(100 * n / total)}%)" for g, n in ranked[:limit]]
    if len(ranked) > limit:
        lines.append(f"• … and {len(ranked) - limit} more group(s)")
    return lines


def _missing_lines(r: dict[str, Any]) -> list[str]:
    eq, p = r["equipment_analysis"], r["parts_analysis"]
    out = []
    missing = eq["fields"]["missing"]
    if _v(missing):
        out.append(f"Fields not in the retrieved data: "
                   f"{', '.join(f.replace('_', ' ') for f in missing['fields'])}")
    for key, label in (("missing_part_numbers", "part records without a part number"),
                       ("missing_descriptions", "part records without a description"),
                       ("missing_names", "part records without a part name"),
                       ("missing_quantities", "part records without a quantity")):
        n = _v(p[key])
        if n:
            out.append(f"{n} {label}")
    for col in ("quantity_required", "description", "where_used", "service_article"):
        if p["total_records"]["value"] and col not in p["columns_published"]:
            out.append(f"'{col.replace('_', ' ')}' is not published in this SIS parts table")
    return out


def render_analysis(r: dict[str, Any], *, focus: str = "ANALYZE_EQUIPMENT") -> str:
    """The full MAIA ANALYSIS report, or one focused section of it."""
    if focus == "RAW_DATA":
        slim = {k: v for k, v in r.items() if k not in ("text",)}
        return "```\n" + json.dumps(slim, indent=2, default=str)[:12000] + "\n```"
    eq, p, q, an = (r["equipment_analysis"], r["parts_analysis"], r["data_quality"],
                    r["anomalies"])
    s = r["summary"]
    insights = r["insights"]
    key = [i["insight"] for i in insights if i["type"] in
           ("DUPLICATE", "STRUCTURAL", "DISTRIBUTION", "CONSISTENCY", "CHANGE")][:6]
    dq = [i["insight"] for i in insights if i["type"] in ("DATA_QUALITY", "FRESHNESS")][:6]
    anomalies = [i["insight"] for i in insights if i["type"] == "ANOMALY"]
    hist = r["history"]
    comp = hist.get("comparison")
    head = [RULE, f"**MAIA ANALYSIS · {r['serial']}**", RULE]

    overview = [f"**Equipment:** {r['serial']}"]
    for label, f in (("Model", "equipment_model"), ("Type", "equipment_type"),
                     ("Machine build date", "machine_build_date"),
                     ("Engine serial", "engine_serial_number"),
                     ("Engine build date", "engine_build_date")):
        if f in eq["equipment"]:
            overview.append(f"**{label}:** {eq['equipment'][f]['value']}")
    if s["equipment_age_years"] is not None:
        overview.append(f"**Equipment age:** {s['equipment_age_years']:g} years "
                        f"(from build date, as of {r['as_of'][:10]})")
    overview += [f"**Data freshness:** {_freshness_line(eq)}",
                 f"**Data completeness:** {s['completeness_pct']:g}% of expected fields "
                 f"({s['fields_populated']} of {s['fields_expected']})",
                 f"**Data quality:** {_quality_line(q)}",
                 f"**Parts:** {s['part_records']} total · {s['unique_part_numbers']} unique · "
                 f"{s['part_groups']} group(s)"]

    changes = []
    if comp:
        if comp["identical_data"]:
            changes.append("No data changes since the previous SIS retrieval.")
        else:
            for label, k in (("new part(s)", "parts_added"), ("removed part(s)", "parts_removed"),
                             ("quantity change(s)", "quantity_changes"),
                             ("field change(s)", "fields_changed"),
                             ("new field(s)", "fields_added"),
                             ("removed field(s)", "fields_removed"),
                             ("metadata change(s)", "metadata_changes")):
                if comp[k]:
                    sign = "+" if k in ("parts_added", "fields_added") else (
                        "−" if k in ("parts_removed", "fields_removed") else "")
                    changes.append(f"{sign}{len(comp[k])} {label}")
        changes.append(f"(previous: {comp['previous']['run_id']} · "
                       f"{_fmt_date(comp['previous']['retrieved_at'])} → latest: "
                       f"{comp['latest']['run_id']} · {_fmt_date(comp['latest']['retrieved_at'])})")
    else:
        changes.append(hist.get("message") or "No history.")

    evidence = (f"All findings are derived from verified SIS data "
                f"({r['snapshot']['source_label']}, run {r['snapshot']['run_id']}, retrieved "
                f"{_fmt_date(r['snapshot']['retrieved_at'])}). Values read from SIS are "
                f"DIRECT; every count, percentage and comparison is DERIVED by Maia's "
                f"analysis engine — no AI model was used.")

    if focus == "SHOW_PARTS":
        body = [f"**Parts in {r['serial']}:** {s['part_records']} record(s), "
                f"{s['unique_part_numbers']} unique part number(s), {s['part_groups']} group(s)",
                "", "**Records per group**", *_group_lines(p, 12)]
        if p["parts_by_category"]["value"]:
            body += ["", f"**By '{p['category_column']}'**"] + [
                f"• {k}: {n}" for k, n in p["parts_by_category"]["value"].items()]
        if p["quantity_total"]["value"] is not None:
            body.append(f"**Quantity total:** {p['quantity_total']['value']:g}")
        return "\n".join(head + body + ["", evidence])
    if focus == "MISSING_DATA":
        return "\n".join(head + ["**Missing data**", *_bullets(_missing_lines(r),
                                                              "Nothing missing.")]
                         + ["", evidence])
    if focus == "DUPLICATE_PARTS":
        dup = p["duplicate_part_numbers"]["value"]
        across = p["repeated_across_groups"]["value"]
        lines = [f"{pn} appears {n} times" + (f" — in {', '.join(across[pn])}"
                                              if pn in across else "")
                 for pn, n in sorted(dup.items(), key=lambda kv: (-kv[1], kv[0]))[:25]]
        return "\n".join(head + [f"**Duplicate part numbers:** {len(dup)} "
                                 f"({_v(p['duplicate_entries'])} extra entries)",
                                 *_bullets(lines, "No part number appears more than once.")]
                         + ["", evidence])
    if focus == "DATA_QUALITY":
        comps = [f"{k.replace('_', ' ')}: {round(100 * v['value'])}% (weight {v.get('weight')})"
                 for k, v in sorted(q["components"].items())]
        return "\n".join(head + [f"**Data quality:** {_quality_line(q)}", "", "**Reasons**",
                                 *_bullets(q["reasons"]), "", "**How it is calculated**",
                                 *_bullets(comps), f"• {q['calculation']}"] + ["", evidence])
    if focus == "SHOW_ANOMALIES":
        method = an["method"].get("rule") or an["method"].get("skipped", "")
        return "\n".join(head + ["**Anomalies (statistical observations about the data)**",
                                 *_bullets(anomalies, "No statistical anomalies."),
                                 f"Method: {method}"] + ["", evidence])
    if focus == "WHAT_CHANGED":
        detail = []
        if comp and not comp["identical_data"]:
            detail += [f"+ {x['part_number']} ({x['group']})" for x in comp["parts_added"][:15]]
            detail += [f"− {x['part_number']} ({x['group']})" for x in comp["parts_removed"][:15]]
            detail += [f"{c['field']}: {c['previous']} → {c['latest']}"
                       for c in comp["fields_changed"][:15]]
            detail += [f"quantity {x['part_number']}: {x['previous']} → {x['latest']}"
                       for x in comp["quantity_changes"][:15]]
        return "\n".join(head + [f"**Since the previous SIS retrieval** "
                                 f"({hist['snapshots']} snapshot(s) stored)",
                                 *_bullets(changes), *(["", "**Details**"]
                                                       + _bullets(detail) if detail else [])]
                         + ["", evidence])
    if focus == "SUMMARY":
        return "\n".join(head + overview + ["", "**Top findings**",
                                            *_bullets([i["insight"] for i in insights][:4])]
                         + ["", evidence])

    body = overview + ["", "**Key findings**", *_bullets(key, "No notable findings."),
                       "", "**Data quality**", *_bullets(dq or q["reasons"][:4]),
                       "", "**Anomalies**", *_bullets(anomalies, "No statistical anomalies."),
                       "", "**Historical changes**", *_bullets(changes),
                       "", "**Largest groups**", *_group_lines(p, 5),
                       "", "**SIS coverage**",
                       *_bullets([f"{c['area']}: {c['status'].replace('_', ' ').lower()}"
                                  for c in r["coverage"]]),
                       "", "**Evidence**", evidence, RULE]
    return "\n".join(head + body)


def render_comparison(c: dict[str, Any]) -> str:
    a, b = c["a"]["serial"], c["b"]["serial"]
    p = c["parts"]
    lines = [RULE, f"**MAIA COMPARISON · {a} vs {b}**", RULE]
    for row in c["attributes"]:
        lines.append(f"• {row['attribute'].replace('_', ' ').capitalize()}: {row['status']}"
                     + (f" ({row['a']} / {row['b']})" if row["status"] == "DIFFERENT" else ""))
    lines += ["", "**Parts**",
              f"• Both machines share {len(p['common'])} part number(s)"
              + (f" ({p['similarity_pct']:g}% overlap)" if p["similarity_pct"] is not None else ""),
              f"• {a} contains {len(p['only_a'])} part number(s) not found in {b}",
              f"• {b} contains {len(p['only_b'])} part number(s) not found in {a}",
              f"• Records: {p['total_a']} vs {p['total_b']} · unique: {p['unique_a']} vs "
              f"{p['unique_b']}"]
    if p["quantity_differences"]:
        lines.append(f"• {len(p['quantity_differences'])} shared part(s) have different "
                     "quantities")
    lines += [f"• Groups in common: {len(c['groups']['common'])} · only {a}: "
              f"{len(c['groups']['only_a'])} · only {b}: {len(c['groups']['only_b'])}",
              "", "**Data quality**",
              f"• {a}: {c['quality']['a'] if c['quality']['a'] is not None else 'insufficient data'}"
              f"{'%' if c['quality']['a'] is not None else ''} · "
              f"{b}: {c['quality']['b'] if c['quality']['b'] is not None else 'insufficient data'}"
              f"{'%' if c['quality']['b'] is not None else ''}",
              f"• Freshness: {a} {c['freshness']['a']['status']} · {b} "
              f"{c['freshness']['b']['status']}",
              "", "**Evidence**",
              f"{a}: run {c['a']['run_id']} ({_fmt_date(c['a']['retrieved_at'])}); "
              f"{b}: run {c['b']['run_id']} ({_fmt_date(c['b']['retrieved_at'])}). "
              "All comparisons are DERIVED from verified SIS data.", RULE]
    return "\n".join(lines)
