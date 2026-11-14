"""Generate a month of fictional fax traffic and the faults to replay it against.

All content is synthetic: fax numbers are random 10-digit numbers starting 555 and fax files are random
bytes behind a PDF or TIFF header (the test bench checks bytes and hashes, not page content).
The notification email layout is illustrative; RingCentral does not publish its exact format.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .fakegraph import FaultPlan, Outage

TZ = ZoneInfo("America/Chicago")
SENDER = "notify@ringcentral.com"   # illustrative; matching is on the ringcentral.com domain


@dataclass
class Email:
    kind: str                    # fax | dup_delivery | dup_notification | resend | no_attachment
    received: datetime
    subject: str
    body: str
    attachments: list = field(default_factory=list)
    internet_message_id: str = ""
    fax_from: str = ""
    pages: int = 0
    of: int | None = None        # index of the original for duplicates and re-sends


@dataclass
class Scenario:
    name: str
    start: datetime
    days: int
    emails: list
    faults: FaultPlan
    host_down: list = field(default_factory=list)     # (start, end): our worker not running
    not_delivered: list = field(default_factory=list) # (start, end): fax service stops emailing (faxes stay in RingCentral)
    p_crash: float = 0.0                              # chance the worker process dies at any request
    incidents: list = field(default_factory=list)     # (label, start, end) that should raise an alert


def _fax_bytes(rng: random.Random, pages: int, tiff: bool) -> bytes:
    head = b"II*\x00" if tiff else b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    return head + rng.randbytes(pages * rng.randint(18_000, 45_000)) + (b"" if tiff else b"\n%%EOF\n")


def _notification(rng: random.Random, t: datetime, fax_from: str, pages: int, tiff: bool, content: bytes):
    lt = t.astimezone(TZ)
    pretty = f"({fax_from[:3]}) {fax_from[3:6]}-{fax_from[6:]}"
    subject = f"New Fax Message from {pretty} on {lt:%m/%d/%Y %I:%M %p}"
    body = (f"You have a new fax message.\n\nFrom: {pretty}\nReceived: {lt:%A, %B %d, %Y at %I:%M %p}\n"
            f"Pages: {pages}\nTo: Intake (ext. 101)\n")
    name = f"fax_{lt:%Y%m%d_%H%M%S}.{'tif' if tiff else 'pdf'}"
    att = {"name": name, "contentType": "image/tiff" if tiff else "application/pdf", "content": content}
    return subject, body, att


def traffic(seed: int, start: datetime, days: int, per_weekday: int = 60) -> list[Email]:
    rng = random.Random(seed)
    senders = [f"555{rng.randint(100, 199):03d}{rng.randint(0, 9999):04d}" for _ in range(120)]
    weights = [1 / (i + 1) for i in range(len(senders))]  # a few physician offices send most faxes
    out: list[Email] = []
    for d in range(days):
        day = (start + timedelta(days=d)).astimezone(TZ)
        n = per_weekday if day.weekday() < 5 else per_weekday // 12
        for _ in range(int(rng.gauss(n, n * 0.15)) if n > 4 else n):
            hour = rng.uniform(7.5, 18.5) if rng.random() < 0.93 else rng.uniform(0, 24)
            t = (day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=hour)).astimezone(timezone.utc)
            fax_from = rng.choices(senders, weights)[0]
            pages = max(1, min(40, int(rng.lognormvariate(1.0, 0.8))))
            tiff = rng.random() < 0.01
            if rng.random() < 0.005:
                subj, body, _ = _notification(rng, t, fax_from, pages, tiff, b"")
                out.append(Email("no_attachment", t, subj, body, [], f"<{rng.getrandbits(64):x}@ringcentral.example>", fax_from, pages))
                continue
            content = _fax_bytes(rng, pages, tiff)
            subj, body, att = _notification(rng, t, fax_from, pages, tiff, content)
            out.append(Email("fax", t, subj, body, [att], f"<{rng.getrandbits(64):x}@ringcentral.example>", fax_from, pages))
    out.sort(key=lambda e: e.received)
    extra = []
    for i, e in enumerate(out):
        if e.kind != "fax":
            continue
        r = rng.random()
        if r < 0.04:      # the same notification delivered to the mailbox twice
            extra.append(Email("dup_delivery", e.received + timedelta(seconds=rng.uniform(1, 90)), e.subject, e.body,
                               e.attachments, e.internet_message_id, e.fax_from, e.pages, i))
        elif r < 0.06:    # a second notification for the same fax (e.g. two extensions notify the mailbox)
            extra.append(Email("dup_notification", e.received + timedelta(seconds=rng.uniform(5, 600)), e.subject,
                               e.body, e.attachments, f"<{rng.getrandbits(64):x}@ringcentral.example>",
                               e.fax_from, e.pages, i))
        elif r < 0.09:    # the sender faxes the same document again: new file, must be kept
            t2 = e.received + timedelta(minutes=rng.uniform(10, 180))
            tiff = e.attachments[0]["contentType"] == "image/tiff"
            content = _fax_bytes(rng, e.pages, tiff)
            subj, body, att = _notification(rng, t2, e.fax_from, e.pages, tiff, content)
            extra.append(Email("resend", t2, subj, body, [att], f"<{rng.getrandbits(64):x}@ringcentral.example>",
                               e.fax_from, e.pages, i))
    out.extend(extra)
    out.sort(key=lambda e: e.received)
    return out


def at(start: datetime, day: int, hour: float) -> datetime:
    """Local business time ``hour`` on simulation day ``day`` as UTC."""
    d = (start + timedelta(days=day)).astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return (d + timedelta(hours=hour)).astimezone(timezone.utc)


def build(name: str, seed: int, start: datetime | None = None, days: int = 30) -> Scenario:
    start = start or datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc)   # Tuesday 1 Sep 2026, 00:00 Chicago
    emails = traffic(seed, start, days)
    if name == "clean":
        return Scenario(name, start, days, emails, FaultPlan())
    everyday = FaultPlan(p_throttle={"mail": 0.01, "files": 0.02, "lists": 0.02},
                         p_error={"mail": 0.005, "files": 0.01, "lists": 0.01}, p_lost=0.005)
    if name == "everyday":
        return Scenario(name, start, days, emails, everyday, p_crash=0.0005)
    if name == "outages":
        rng = random.Random(seed + 7)

        def win(day, h0, dur_h):
            s = at(start, day, h0 + rng.uniform(-0.5, 0.5))
            return s, s + timedelta(hours=dur_h)
        # day 0 is Tuesday 1 September 2026; all incidents fall on weekdays
        sp = win(7, 10, 4)          # Tue: SharePoint down 4 h in the morning
        mail = win(14, 14, 2)       # Tue: mailbox API down 2 h
        lists = win(16, 9, 1.5)     # Thu: lists only (register) down 1.5 h
        night = win(20, 21, 11)     # Mon night: our worker host down 11 h
        day_ = win(22, 11, 3)       # Wed: our worker host down 3 h in business hours
        quiet = win(23, 10, 4)      # Thu: fax notifications stop arriving for 4 h (mail rule or RingCentral setting)
        perm = win(27, 8.5, 6)      # Mon: client secret expired, 403 for 6 h until an admin renews it
        f = FaultPlan(p_throttle=everyday.p_throttle, p_error=everyday.p_error, p_lost=everyday.p_lost,
                      outages=[Outage("files", *sp), Outage("mail", *mail), Outage("lists", *lists),
                               Outage("all", *perm, status=403)])
        inc = [("sharepoint_outage", *sp), ("mail_outage", *mail), ("register_outage", *lists),
               ("host_down_night", *night), ("host_down_day", *day_), ("notifications_stopped", *quiet),
               ("access_denied", *perm)]
        return Scenario(name, start, days, emails, f, host_down=[night, day_], not_delivered=[quiet],
                        p_crash=0.0005, incidents=inc)
    raise ValueError(name)
