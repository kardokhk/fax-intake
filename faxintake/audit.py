"""Audit trail and alerts.

Every step writes one event row to the "Fax Audit Log" SharePoint list. If SharePoint is
unreachable the event is kept in a local outbox file and written on the next run, so an outage
delays audit rows but never loses them. Alerts are emails to the administrators; the same
incident is not repeated within ``alert_repeat_hours``. No alert contains patient information:
only times, fax numbers, counts and links to the register.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from .clock import Clock, iso
from .config import Settings
from .graph import GraphClient, GraphError


class AuditLog:
    def __init__(self, g: GraphClient, s: Settings, clock: Clock, run_id: str, state_dir: Path):
        self.g, self.s, self.clock, self.run_id = g, s, clock, run_id
        self.outbox = Path(state_dir) / "audit_outbox.jsonl"
        self.outbox.parent.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, *, message_id: str = "", internet_message_id: str = "", sha256: str = "",
              detail: str = "", incident: str = "") -> None:
        row = {"Title": event, "Event": event, "EventTime": iso(self.clock.now()), "RunId": self.run_id,
               "MessageId": message_id, "InternetMessageId": internet_message_id, "ContentSHA256": sha256,
               "Detail": detail[:1000], "IncidentKey": incident}
        try:
            self.g.create_item(self.s.site_id, self.s.audit_list, row)
        except GraphError:
            with self.outbox.open("a") as f:
                f.write(json.dumps(row) + "\n")

    def flush(self) -> int:
        if not self.outbox.exists():
            return 0
        rows = [json.loads(x) for x in self.outbox.read_text().splitlines() if x.strip()]
        left = []
        for i, row in enumerate(rows):
            try:
                self.g.create_item(self.s.site_id, self.s.audit_list, row)
            except GraphError:
                left = rows[i:]
                break
        self.outbox.write_text("".join(json.dumps(r) + "\n" for r in left))
        return len(rows) - len(left)


class Alerter:
    def __init__(self, g: GraphClient, s: Settings, clock: Clock, audit: AuditLog, state_dir: Path):
        self.g, self.s, self.clock, self.audit = g, s, clock, audit
        self.state = Path(state_dir) / "alerts_sent.json"

    def _sent(self) -> dict:
        try:
            return json.loads(self.state.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def alert(self, incident: str, subject: str, body: str) -> bool:
        """Send an alert unless the same incident was alerted recently. Returns True if sent."""
        sent = self._sent()
        now = self.clock.now()
        last = sent.get(incident)
        if last and now.timestamp() - last < self.s.alert_repeat_hours * 3600:
            return False
        try:
            self.g.send_mail(self.s.mailbox, self.s.alert_to, f"[Fax intake] {subject}", body)
        except GraphError as e:
            # mail itself is down; the external heartbeat monitor covers this case
            self.audit.event("alert_failed", detail=f"{subject}: {e}", incident=incident)
            return False
        sent[incident] = now.timestamp()
        self.state.write_text(json.dumps(sent))
        self.audit.event("alert_sent", detail=subject, incident=incident)
        return True
