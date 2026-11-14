"""Daily summary and integrity check, sent to the administrators each morning.

Re-reads every file saved in the last ``days`` days from SharePoint and compares its QuickXorHash
with the value recorded at save time, so a deleted, replaced or edited fax is reported the next
morning. Counts come from the register and the audit log; no patient information is included.
"""
from __future__ import annotations

from datetime import timedelta

from .clock import iso
from .graph import GraphError, NotFound
from .processor import FaxIntake


def daily_summary(fi: FaxIntake, days: int = 1) -> dict:
    s, g, now = fi.s, fi.g, fi.clock.now()
    lo = iso(now - timedelta(days=days))
    rows = [r["fields"] for r in g.find_items(s.site_id, s.register_list, f"fields/ReceivedAt ge '{lo}'", top=5000)]
    ev = [r["fields"] for r in g.find_items(s.site_id, s.audit_list, f"fields/EventTime ge '{lo}'", top=20000)]
    count = lambda k: sum(1 for e in ev if e["Event"] == k)
    bad = []
    for r in rows:
        if r["Status"] != "Saved":
            continue
        try:
            it = g.item_by_id(s.drive_id, r["FileItemId"])
            if it.get("file", {}).get("hashes", {}).get("quickXorHash") != r["QuickXorHash"]:
                bad.append(f"changed: {r['TargetPath']}")
        except NotFound:
            bad.append(f"missing: {r['TargetPath']}")
        except GraphError as e:
            bad.append(f"not checked ({e.status}): {r['TargetPath']}")
    out = {"window_start": lo, "faxes_saved": sum(1 for r in rows if r["Status"] == "Saved"),
           "not_saved_yet": sum(1 for r in rows if r["Status"] != "Saved"),
           "duplicates_skipped": count("duplicate_skipped"), "possible_resends": count("possible_resend"),
           "emails_to_review": count("no_attachment"), "retries_after_failure": count("claim_taken_over"),
           "alerts_sent": count("alert_sent"), "integrity_problems": bad}
    body = "\n".join([f"Fax intake summary since {lo} (UTC)", "",
                      f"Faxes saved and verified:      {out['faxes_saved']}",
                      f"Not saved yet (retrying):      {out['not_saved_yet']}",
                      f"Duplicate copies skipped:      {out['duplicates_skipped']}",
                      f"Possible re-sends (both kept): {out['possible_resends']}",
                      f"Emails moved to review:        {out['emails_to_review']}",
                      f"Retries after a failed try:    {out['retries_after_failure']}",
                      f"Alerts sent:                   {out['alerts_sent']}",
                      f"Integrity check:               {'all files unchanged' if not bad else str(len(bad)) + ' problem(s)'}"]
                     + [f"  - {b}" for b in bad])
    subject = "Daily summary" + (" - ACTION NEEDED" if bad or out["not_saved_yet"] else "")
    g.send_mail(s.mailbox, s.alert_to, f"[Fax intake] {subject}", body)
    fi.audit.event("daily_summary", detail=body[:1000])
    return out
