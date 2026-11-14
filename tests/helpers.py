"""Shared fixtures: a fake tenant with the intake folders and lists, and a fax email factory."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import httpx

from faxintake.clock import SimClock
from faxintake.config import Settings
from faxintake.graph import GraphClient
from faxintake.processor import FaxIntake
from sim.fakegraph import FakeGraph, FaultPlan

T0 = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc)   # Tuesday 10:00 in Chicago


def tenant(faults: FaultPlan | None = None, seed: int = 0):
    s = Settings()
    clock = SimClock(T0)
    fg = FakeGraph(clock, s.mailbox, faults, seed=seed)
    fg.add_folder(s.saved_folder)
    fg.add_folder(s.review_folder)
    fg.add_list(s.register_list, unique="ContentSHA256")
    fg.add_list(s.audit_list)
    return s, clock, fg


def client(fg: FakeGraph, clock, handler=None) -> GraphClient:
    t = httpx.MockTransport(handler or fg.handle)
    return GraphClient(httpx.Client(transport=t, base_url="https://graph.microsoft.com/v1.0"), clock, rng=random.Random(0))


def service(s, clock, fg, tmp_path, run_id="r1", handler=None) -> FaxIntake:
    return FaxIntake(client(fg, clock, handler), s, clock, tmp_path, run_id=run_id)


def pdf(seed: int, n: int = 5000) -> bytes:
    return b"%PDF-1.4\n" + random.Random(seed).randbytes(n) + b"\n%%EOF\n"


def fax_email(content: bytes | None, minutes: float = 0, imid: str | None = None, fax_from="5550142231", pages=2,
              sender="notify@ringcentral.com"):
    t = T0 - timedelta(minutes=10) + timedelta(minutes=minutes)
    pretty = f"({fax_from[:3]}) {fax_from[3:6]}-{fax_from[6:]}"
    atts = [] if content is None else [{"name": "fax.pdf", "contentType": "application/pdf", "content": content}]
    return {"subject": f"New Fax Message from {pretty}", "from": sender, "received": t,
            "body": f"From: {pretty}\nPages: {pages}\n", "attachments": atts, "internetMessageId": imid}


def events(fg, s, kind=None):
    ev = [i["fields"] for i in fg.lists[s.audit_list]["items"].values()]
    return [e for e in ev if kind is None or e["Event"] == kind]
