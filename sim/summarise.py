"""Summarise runs/<scenario>_s<seed>.json into a markdown table and runs/summary.json."""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def main(d: str) -> None:
    runs = sorted(Path(d).glob("*_s*.json"))
    by = {}
    for p in runs:
        r = json.loads(p.read_text())
        by.setdefault(r["service"]["scenario"], []).append(r)
    summary = {}
    lines = ["| Scenario | Seeds | Fax emails | Unique fax files | Service: missing | Service: duplicate files | "
             "Basic flow: missing | Basic flow: duplicate files | Basic flow: failed runs, no alert |",
             "|---|---|---|---|---|---|---|---|---|"]
    for scn in ("clean", "everyday", "outages"):
        rs = by.get(scn, [])
        if not rs:
            continue
        S = lambda k, a="service": sum(r[a][k] for r in rs)
        row = {"seeds": len(rs), "emails": S("emails"), "unique": S("unique_fax_files"),
               "svc_missing": S("missing"), "svc_dup": S("duplicate_files"), "svc_altered": S("altered_or_unknown"),
               "svc_overwrites": S("overwrites"), "svc_left_in_inbox": S("emails_left_in_inbox"),
               "svc_outcome_events": S("emails_with_outcome_event"), "svc_audit_outbox_left": S("audit_outbox_left"),
               "svc_no_attachment": S("no_attachment"), "svc_no_attachment_to_review": S("no_attachment_to_review"),
               "resends": S("resends"), "resends_flagged": S("resends_flagged"), "resend_flags_total": S("resend_flags_total"),
               "svc_crashes": S("crashes"), "svc_retries": S("retries"),
               "injected": {k: sum(r["service"]["injected"][k] for r in rs) for k in rs[0]["service"]["injected"]},
               "unexplained_alerts": sum(len(r["service"]["alerts_unexplained"]) for r in rs),
               "alerts_total": sum(len(r["service"]["alerts"]) for r in rs),
               "basic_missing": S("missing", "basic_flow"), "basic_dup": S("duplicate_files", "basic_flow"),
               "basic_failed_runs": S("failed_runs_silent", "basic_flow"),
               "basic_no_attachment_unreported": S("no_attachment", "basic_flow"),
               "not_delivered": S("emails_not_delivered_by_fax_service")}
        lat = [r["service"]["minutes_to_save_normal"] for r in rs if r["service"]["minutes_to_save_normal"]]
        row["latency_normal_median_of_medians"] = statistics.median(x["median"] for x in lat)
        row["latency_normal_max_p95"] = max(x["p95"] for x in lat)
        row["latency_normal_max"] = max(x["max"] for x in lat)
        li = [r["service"]["minutes_to_save_during_incidents"] for r in rs if r["service"]["minutes_to_save_during_incidents"]]
        if li:
            row["latency_incident_max"] = max(x["max"] for x in li)
            row["latency_incident_n"] = sum(x["n"] for x in li)
        inc = {}
        for r in rs:
            for i in r["service"]["incidents"]:
                inc.setdefault(i["incident"], []).append(i)
        row["incidents"] = {k: {"n": len(v), "alerted": sum(x["alerted"] for x in v),
                                "duration_min": round(statistics.median(x["duration_min"] for x in v)),
                                "minutes_to_alert_median": statistics.median(x["minutes_to_first_alert"] for x in v if x["alerted"])
                                if any(x["alerted"] for x in v) else None,
                                "minutes_to_alert_max": max((x["minutes_to_first_alert"] for x in v if x["alerted"]), default=None),
                                "first_alert_kinds": sorted({x["first_alert"] for x in v if x["first_alert"]})}
                            for k, v in inc.items()}
        summary[scn] = row
        lines.append(f"| {scn} | {len(rs)} | {row['emails']:,} | {row['unique']:,} | {row['svc_missing']} | {row['svc_dup']} | "
                     f"{row['basic_missing']} | {row['basic_dup']} | {row['basic_failed_runs']} |")
    Path(d, "summary.json").write_text(json.dumps(summary, indent=1))
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1])
