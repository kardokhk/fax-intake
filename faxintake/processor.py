"""Fax intake run: find fax emails, store each fax file once, record everything, file the email.

One run is a sweep over every fax email still in the source folder within the look-back window.
Because a run only acts on what is still unfinished, running it every few minutes is both the
normal path and the recovery path: anything a failed or missed run left behind is picked up by
the next one, with no separate replay step.

Per fax file the order of operations is:
  1. claim:   create a register row keyed on the file's SHA-256 (the column enforces unique values)
  2. store:   upload to SharePoint with conflictBehavior=fail at a path derived from the row
  3. verify:  compare SharePoint's size and QuickXorHash with the bytes read from the email
  4. record:  mark the register row Saved, write audit events
and per email, once all its files are Saved or recognised as duplicates:
  5. file:    move the email to the "Saved" folder (never deleted)
A crash between any two steps leaves a state the next run completes without creating a second copy.
"""
from __future__ import annotations

import hashlib
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import notification
from .audit import Alerter, AuditLog
from .clock import Clock, iso, parse_iso
from .config import Settings
from .graph import Attachment, Conflict, GraphClient, GraphError, NotFound
from .naming import fax_path
from .quickxor import quickxorhash


@dataclass
class RunReport:
    run_id: str
    emails_seen: int = 0
    files_saved: int = 0
    duplicates: int = 0
    resumed: int = 0
    deferred: int = 0
    failed: int = 0
    emails_filed: int = 0
    needs_review: int = 0
    alerts: int = 0
    listing_ok: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        """What the run reports to the external heartbeat monitor. A run that could not read the
        mailbox or could not save a file is unhealthy; the monitor alerts if no healthy run arrives
        for 20 minutes. This covers failures that also stop the service's own email alerts
        (mail outage, expired secret, the host itself being down)."""
        return self.listing_ok and self.failed == 0


class FaxIntake:
    def __init__(self, g: GraphClient, s: Settings, clock: Clock, state_dir: str | Path, run_id: str | None = None):
        self.g, self.s, self.clock = g, s, clock
        self.run_id = run_id or f"{socket.gethostname()}-{clock.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        self.audit = AuditLog(g, s, clock, self.run_id, Path(state_dir))
        self.alerter = Alerter(g, s, clock, self.audit, Path(state_dir))
        self._folders: dict[str, str] = {}

    # ------------------------------------------------------------------------------------------
    def folder(self, name: str) -> str:
        if name not in self._folders:
            self._folders[name] = self.g.folder_id(self.s.mailbox, name)
        return self._folders[name]

    def run(self) -> RunReport:
        rep = RunReport(self.run_id)
        try:
            self.audit.flush()
            since = iso(self.clock.now() - timedelta(days=self.s.lookback_days))
            msgs = self.g.list_messages(self.s.mailbox, self.folder(self.s.source_folder), since)
            rep.listing_ok = True
        except GraphError as e:
            rep.errors.append(f"listing failed: {e}")
            self._maybe_alert_blocked(e, rep)
            return rep
        for m in msgs:
            if not notification.is_fax_sender(m.get("from", {}).get("emailAddress", {}).get("address", ""),
                                              self.s.sender_domains):
                continue
            rep.emails_seen += 1
            try:
                self.process_message(m, rep)
            except GraphError as e:
                rep.errors.append(f"{m['id']}: {e}")
                self._maybe_alert_blocked(e, rep)
        self.check_overdue(rep)
        return rep

    def _maybe_alert_blocked(self, e: GraphError, rep: RunReport) -> None:
        # permission problems never fix themselves: alert at once rather than waiting for the overdue rule
        if e.status in (401, 403):
            if self.alerter.alert("access-denied", "Access to Microsoft 365 was refused",
                                  f"The fax intake service could not read or write: {e.status} {e.code}.\n"
                                  "Likely cause: an expired client secret or a removed permission. "
                                  "Faxes keep arriving in the mailbox and will be saved once access is restored."):
                rep.alerts += 1

    # ------------------------------------------------------------------------------------------
    def process_message(self, m: dict, rep: RunReport) -> None:
        mid, imid = m["id"], m.get("internetMessageId", "")
        files = [a for a in self.g.attachments(self.s.mailbox, mid)
                 if a.name.lower().endswith(self.s.file_types) or a.content_type in ("application/pdf", "image/tiff")]
        if not files:
            self.audit.event("no_attachment", message_id=mid, internet_message_id=imid,
                             detail="Fax email without a PDF or TIFF attachment. If RingCentral's HIPAA setting "
                                    "is on, fax content is not attached and must be fetched another way.")
            self.g.move_message(self.s.mailbox, mid, self.folder(self.s.review_folder))
            rep.needs_review += 1
            if self.alerter.alert(f"no-attachment-{mid}", "Fax email arrived without a fax file",
                                  f"An email from the fax service, received {m.get('receivedDateTime')}, has no PDF or "
                                  f"TIFF attached. It was moved to '{self.s.review_folder}' for a person to check."):
                rep.alerts += 1
            return
        info = notification.parse(m.get("subject", ""), (m.get("body") or {}).get("content", ""))
        received = parse_iso(m["receivedDateTime"])
        outcomes = [self.process_file(m, a, i, info, received, rep) for i, a in enumerate(files)]
        if all(o in ("saved", "duplicate", "resumed") for o in outcomes):
            self.g.move_message(self.s.mailbox, mid, self.folder(self.s.saved_folder))
            self.audit.event("email_filed", message_id=mid, internet_message_id=imid,
                             detail=f"{len(files)} file(s): {', '.join(outcomes)}; moved to '{self.s.saved_folder}'")
            rep.emails_filed += 1

    def process_file(self, m: dict, a: Attachment, idx: int, info: notification.FaxInfo,
                     received: datetime, rep: RunReport) -> str:
        mid, imid = m["id"], m.get("internetMessageId", "")
        sha = hashlib.sha256(a.content).hexdigest()
        ext = "." + a.name.rsplit(".", 1)[-1].lower() if "." in a.name else ".pdf"
        path = fax_path(self.s.base_path, received, self.s.timezone, info.fax_from, info.pages, sha, ext)
        now = self.clock.now()
        row = {"Title": path.rsplit("/", 1)[-1], "ContentSHA256": sha, "Status": "Claimed", "ClaimedAt": iso(now),
               "ClaimedBy": self.run_id, "Attempts": 1, "MessageId": mid, "InternetMessageId": imid,
               "ReceivedAt": iso(received), "FaxFrom": info.fax_from or "", "Pages": info.pages or 0,
               "SizeBytes": len(a.content), "TargetPath": path, "DuplicateCount": 0}
        # 1. claim ---------------------------------------------------------------------------
        try:
            item = self.g.create_item(self.s.site_id, self.s.register_list, row)
            item_id = item["id"]
            self.audit.event("received", message_id=mid, internet_message_id=imid, sha256=sha,
                             detail=f"attachment {idx + 1}, {len(a.content)} bytes")
            resumed = False
        except Conflict:
            existing = self._register_row(sha)
            if existing is None:  # lost response on our own create: the row may appear shortly
                rep.deferred += 1
                return "deferred"
            f = existing["fields"]
            if f["Status"] == "Saved":
                if f.get("MessageId") == mid:
                    # this email's file was saved by an earlier run that stopped before filing the email
                    self.audit.event("saved", message_id=mid, internet_message_id=imid, sha256=sha,
                                     detail=f"{f['TargetPath']} confirmed (stored by an interrupted earlier run)")
                    rep.resumed += 1
                    return "resumed"
                self._record_duplicate(existing, m, sha, rep)
                return "duplicate"
            claimed_at = parse_iso(f["ClaimedAt"])
            stale = now - claimed_at > timedelta(minutes=self.s.claim_lease_minutes)
            if f["Status"] == "Claimed" and not stale and f.get("ClaimedBy") != self.run_id:
                rep.deferred += 1  # another run is saving this file right now
                return "deferred"
            try:  # take over a stale or failed claim; eTag stops two runs taking over together
                upd = self.g.update_item(self.s.site_id, self.s.register_list, existing["id"],
                                         {"Status": "Claimed", "ClaimedAt": iso(now), "ClaimedBy": self.run_id,
                                          "Attempts": int(f.get("Attempts", 1)) + 1}, etag=existing.get("eTag"))
            except Conflict:
                rep.deferred += 1
                return "deferred"
            item_id, path = existing["id"], f["TargetPath"]
            self.audit.event("claim_taken_over", message_id=mid, sha256=sha,
                             detail=f"previous status {f['Status']} by {f.get('ClaimedBy')}, attempt {int(f.get('Attempts', 1)) + 1}")
            resumed = True
        # 2. store and 3. verify ---------------------------------------------------------------
        try:
            try:
                di = self.g.upload_new(self.s.drive_id, path, a.content)
            except Conflict:
                di = self.g.item_by_path(self.s.drive_id, path)  # an earlier attempt stored it; check it is ours
            qx = quickxorhash(a.content)
            if di.get("size") != len(a.content) or di.get("file", {}).get("hashes", {}).get("quickXorHash") != qx:
                raise IntegrityError(f"stored file at {path} does not match the fax received "
                                     f"(size {di.get('size')} vs {len(a.content)})")
            # 4. record ------------------------------------------------------------------------
            self.g.update_item(self.s.site_id, self.s.register_list, item_id,
                               {"Status": "Saved", "SavedAt": iso(self.clock.now()), "FileItemId": di["id"],
                                "FileUrl": di.get("webUrl", ""), "QuickXorHash": qx, "LastError": ""})
        except (GraphError, IntegrityError) as e:
            self._mark_failed(item_id, sha, mid, e, rep)
            return "failed"
        self.audit.event("saved", message_id=mid, internet_message_id=imid, sha256=sha,
                         detail=f"{path} verified (size and QuickXorHash){' after takeover' if resumed else ''}")
        rep.files_saved += 1
        self._check_resend(item_id, info, received, sha, mid)
        return "saved"

    # ------------------------------------------------------------------------------------------
    def _register_row(self, sha: str) -> dict | None:
        rows = self.g.find_items(self.s.site_id, self.s.register_list, f"fields/ContentSHA256 eq '{sha}'", top=1)
        return rows[0] if rows else None

    def _record_duplicate(self, existing: dict, m: dict, sha: str, rep: RunReport) -> None:
        f = existing["fields"]
        try:
            self.g.update_item(self.s.site_id, self.s.register_list, existing["id"],
                               {"DuplicateCount": int(f.get("DuplicateCount", 0)) + 1})
        except GraphError:
            pass  # the counter is informational; the audit event below is the record
        self.audit.event("duplicate_skipped", message_id=m["id"], internet_message_id=m.get("internetMessageId", ""),
                         sha256=sha, detail=f"identical to {f['TargetPath']} (first received {f['ReceivedAt']})")
        rep.duplicates += 1

    def _mark_failed(self, item_id: str, sha: str, mid: str, e: Exception, rep: RunReport) -> None:
        rep.failed += 1
        rep.errors.append(f"{mid}: {e}")
        try:
            self.g.update_item(self.s.site_id, self.s.register_list, item_id,
                               {"Status": "Failed", "LastError": str(e)[:500]})
        except GraphError:
            pass  # the claim goes stale and is taken over by a later run
        self.audit.event("save_failed", message_id=mid, sha256=sha, detail=str(e))
        if isinstance(e, IntegrityError):
            if self.alerter.alert(f"integrity-{sha[:12]}", "Stored fax does not match the fax received", str(e)):
                rep.alerts += 1

    def _check_resend(self, item_id: str, info: notification.FaxInfo, received: datetime, sha: str, mid: str) -> None:
        """Flag a different file from the same fax number with the same page count shortly before:
        usually the sender faxing the same document again. Both are kept; a person decides."""
        if not info.fax_from:
            return
        lo = iso(received - timedelta(hours=self.s.resend_window_hours))
        try:
            rows = self.g.find_items(self.s.site_id, self.s.register_list,
                                     f"fields/FaxFrom eq '{info.fax_from}' and fields/ReceivedAt ge '{lo}'")
        except GraphError:
            return
        for r in rows:
            f = r["fields"]
            if f["ContentSHA256"] != sha and int(f.get("Pages") or 0) == (info.pages or 0) \
                    and parse_iso(f["ReceivedAt"]) <= received:
                try:
                    self.g.update_item(self.s.site_id, self.s.register_list, item_id, {"PossibleResendOf": f["TargetPath"]})
                except GraphError:
                    pass
                self.audit.event("possible_resend", message_id=mid, sha256=sha,
                                 detail=f"same sender and page count as {f['TargetPath']}; both kept")
                return

    # ------------------------------------------------------------------------------------------
    def check_overdue(self, rep: RunReport) -> None:
        """Alert when any fax email has waited longer than ``overdue_minutes`` without being saved."""
        try:
            since = iso(self.clock.now() - timedelta(days=self.s.lookback_days))
            left = [m for m in self.g.list_messages(self.s.mailbox, self.folder(self.s.source_folder), since)
                    if notification.is_fax_sender(m.get("from", {}).get("emailAddress", {}).get("address", ""),
                                                  self.s.sender_domains)]
        except GraphError:
            return
        cutoff = self.clock.now() - timedelta(minutes=self.s.overdue_minutes)
        late = [m for m in left if parse_iso(m["receivedDateTime"]) < cutoff]
        if late:
            oldest = min(m["receivedDateTime"] for m in late)
            if self.alerter.alert("overdue", f"{len(late)} fax(es) not yet saved",
                                  f"{len(late)} fax email(s) have waited more than {self.s.overdue_minutes} minutes. "
                                  f"Oldest received {oldest} (UTC). The service keeps retrying every run; recent errors:\n"
                                  + "\n".join(rep.errors[-5:])):
                rep.alerts += 1

    def check_silence(self, now: datetime | None = None) -> bool:
        """During business hours, alert if no fax email at all has arrived for ``silence_hours``.
        Catches a broken RingCentral notification, a changed mail rule or a redirected number."""
        now = now or self.clock.now()
        local = now.astimezone(ZoneInfo(self.s.timezone))
        start_local = (now - timedelta(hours=self.s.silence_hours)).astimezone(ZoneInfo(self.s.timezone))
        h0, h1 = self.s.business_hours
        if local.weekday() >= 5 or start_local.date() != local.date() or not (h0 <= start_local.hour and local.hour < h1):
            return False
        since = iso(now - timedelta(hours=self.s.silence_hours))
        try:
            n = 0
            for name in (self.s.source_folder, self.s.saved_folder, self.s.review_folder):
                n += len(self.g.list_messages(self.s.mailbox, self.folder(name), since))
        except GraphError:
            return False
        if n == 0:
            return self.alerter.alert("silence", f"No faxes received for {self.s.silence_hours:g} hours",
                                      "No fax email has arrived during business hours for "
                                      f"{self.s.silence_hours:g} hours. Check that RingCentral still sends fax "
                                      "notifications to the intake mailbox.")
        return False


class IntegrityError(Exception):
    pass
