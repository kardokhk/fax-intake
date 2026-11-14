"""Replay a scenario against the fax intake service and against a basic flow; measure the outcome.

usage: python -m sim.run --scenario outages --seed 1 --out runs/outages_s1.json
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import random
import statistics
import tempfile
from datetime import timedelta
from pathlib import Path

import httpx

from faxintake.clock import SimClock, iso, parse_iso
from faxintake.config import Settings
from faxintake.graph import GraphClient
from faxintake.processor import FaxIntake

from . import scenario as sc
from .fakegraph import FakeGraph

TICK = timedelta(minutes=5)
MONITOR_GAP = timedelta(minutes=20)   # external heartbeat monitor: alert if no completed run for this long


class Crash(BaseException):
    """The worker process dies mid-run (power cut, killed process). Not an Exception on purpose."""


def _setup(clock: SimClock, s: Settings, faults, seed: int) -> FakeGraph:
    fg = FakeGraph(clock, s.mailbox, faults, seed=seed)
    for f in (s.saved_folder, s.review_folder):
        fg.add_folder(f)
    fg.add_list(s.register_list, unique="ContentSHA256")
    fg.add_list(s.audit_list)
    return fg


def _deliver(fg: FakeGraph, e: sc.Email) -> str:
    return fg.deliver({"subject": e.subject, "from": sc.SENDER, "received": e.received, "body": e.body,
                       "attachments": e.attachments, "internetMessageId": e.internet_message_id})


def _in(t, windows) -> bool:
    return any(a <= t < b for a, b in windows)


# ------------------------------------------------------------------------------------------------
def run_service(scn: sc.Scenario, seed: int) -> dict:
    s = Settings(timezone="America/Chicago")
    clock = SimClock(scn.start)
    fg = _setup(clock, s, scn.faults, seed)
    crash_rng = random.Random(seed + 99)
    base = fg.handle

    def handler(req):
        if crash_rng.random() < scn.p_crash:
            raise Crash()
        return base(req)

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://graph.microsoft.com/v1.0")
    state = Path(tempfile.mkdtemp(prefix="faxsim-"))
    end = scn.start + timedelta(days=scn.days + 1)
    pending = list(scn.emails)
    msg_of: dict[int, str] = {}
    t = scn.start
    last_ok, monitor_alerts, crashes, runs, retries = scn.start, [], 0, 0, 0
    gap_open = False
    while t < end:
        while pending and pending[0].received <= t:
            e = pending.pop(0)
            if not _in(e.received, scn.not_delivered):
                msg_of[id(e)] = _deliver(fg, e)
        clock.set(t)
        if not _in(t, scn.host_down):
            g = GraphClient(http, clock, max_retries=s.max_retries, rng=random.Random(runs))
            fi = FaxIntake(g, s, clock, state, run_id=f"run{runs:05d}")
            runs += 1
            try:
                rep = fi.run()
                if t.minute < 5 and t < scn.start + timedelta(days=scn.days):
                    fi.check_silence()
                if rep.healthy:   # heartbeat ping to the external monitor
                    last_ok = clock.now()
                    gap_open = False
            except Crash:
                crashes += 1
            retries += g.retries
        if clock.now() - last_ok > MONITOR_GAP and not gap_open:
            monitor_alerts.append({"time": iso(clock.now()), "subject": "[Monitor] no healthy fax intake run for 20 minutes"})
            gap_open = True
        t = max(t + TICK, clock.now())
    # final flush of the audit outbox with faults off
    fg.faults = sc.FaultPlan()
    FaxIntake(GraphClient(http, clock), s, clock, state, run_id="final").audit.flush()
    outbox_left = sum(1 for _ in (state / "audit_outbox.jsonl").open()) if (state / "audit_outbox.jsonl").exists() else 0
    return _measure(scn, fg, s, msg_of, monitor_alerts, extra={"runs": runs, "crashes": crashes, "retries": retries,
                                                                  "audit_outbox_left": outbox_left})


# ------------------------------------------------------------------------------------------------
def run_basic_flow(scn: sc.Scenario, seed: int) -> dict:
    """The common starter flow: 'When a new email arrives (with attachments)' -> 'Create file' per
    attachment, file named <received time>_<attachment name>, default retry policy for flows on
    Microsoft 365 licences (2 retries, about 5 then 10 minutes apart), polling every 5 minutes with
    catch-up after an outage, no duplicate check, no alerting beyond the platform defaults."""
    s = Settings(timezone="America/Chicago")
    clock = SimClock(scn.start)
    fg = _setup(clock, s, scn.faults, seed)
    http = httpx.Client(transport=fg.transport(), base_url="https://graph.microsoft.com/v1.0")
    end = scn.start + timedelta(days=scn.days + 1)
    pending = list(scn.emails)
    msg_of: dict[int, str] = {}
    seen: set[str] = set()
    queue: list = []   # (time, seq, message_id, attempt)
    seq = 0
    failed_runs = 0
    t = scn.start
    while t < end:
        while pending and pending[0].received <= t:
            e = pending.pop(0)
            if not _in(e.received, scn.not_delivered):
                msg_of[id(e)] = _deliver(fg, e)
        clock.set(t)
        try:  # trigger poll: new messages with attachments
            # the platform trigger remembers where it stopped; model that with a 1-day look-back plus seen ids
            url, params = f"/users/{s.mailbox}/mailFolders/inbox/messages", {
                "$filter": f"receivedDateTime ge {iso(t - timedelta(days=1))}", "$top": "100"}
            new = []
            while url:
                r = http.get(url, params=params)
                if r.status_code != 200:
                    new = []
                    break
                d = r.json()
                new.extend(d["value"])
                url, params = d.get("@odata.nextLink"), None
            for m in new:
                if m["id"] not in seen and m["hasAttachments"]:
                    seen.add(m["id"])
                    heapq.heappush(queue, (t, seq, m["id"], m["receivedDateTime"], 0))
                    seq += 1
        except httpx.TransportError:
            pass
        while queue and queue[0][0] <= t:
            _, _, mid, rcv, attempt = heapq.heappop(queue)
            ok = _basic_run(http, fg, s, mid, rcv)
            if not ok:
                if attempt < 2:
                    heapq.heappush(queue, (t + timedelta(minutes=5 * (attempt + 1)), seq, mid, rcv, attempt + 1))
                    seq += 1
                else:
                    failed_runs += 1
        t = t + TICK
    return _measure(scn, fg, s, msg_of, [], extra={"failed_runs_silent": failed_runs}, basic=True)


def _basic_run(http, fg, s, mid, rcv) -> bool:
    try:
        r = http.get(f"/users/{s.mailbox}/messages/{mid}/attachments")
        if r.status_code != 200:
            return False
        for a in r.json()["value"]:
            name = f"{parse_iso(rcv):%Y-%m-%d_%H%M%S}_{a['name']}"
            import base64
            w = http.put(f"/drives/{s.drive_id}/root:/Fax/{name}:/content",
                         params={"@microsoft.graph.conflictBehavior": "fail"}, content=base64.b64decode(a["contentBytes"]))
            if w.status_code == 409:
                continue   # an earlier attempt of this run already created it
            if w.status_code >= 300:
                return False
        return True
    except httpx.TransportError:
        return False


# ------------------------------------------------------------------------------------------------
def _measure(scn, fg: FakeGraph, s: Settings, msg_of, monitor_alerts, extra, basic=False) -> dict:
    undelivered = [e for e in scn.emails if id(e) not in msg_of]
    emails = [e for e in scn.emails if id(e) in msg_of]
    expected = {}
    for e in emails:
        for a in e.attachments:
            h = hashlib.sha256(a["content"]).hexdigest()
            expected.setdefault(h, e.received)
    stored: dict[str, list] = {}
    for p, it in fg.files.items():
        stored.setdefault(hashlib.sha256(it["_content"]).hexdigest(), []).append(p)
    writes_per_path: dict[str, int] = {}
    for _, p, _ in fg.file_writes:
        writes_per_path[p] = writes_per_path.get(p, 0) + 1
    missing = [h for h in expected if h not in stored]
    dup_files = sum(len(v) - 1 for v in stored.values())
    foreign = [h for h in stored if h not in expected]
    res = {"scenario": scn.name, "approach": "basic_flow" if basic else "service",
           "emails": len(emails), "emails_not_delivered_by_fax_service": len(undelivered), "emails_by_kind": _count(e.kind for e in emails),
           "unique_fax_files": len(expected), "stored_files": sum(len(v) for v in stored.values()),
           "missing": len(missing), "duplicate_files": dup_files, "altered_or_unknown": len(foreign),
           "overwrites": sum(v - 1 for v in writes_per_path.values()),
           "requests": fg.requests, "injected": dict(fg.injected), **extra}
    alerts = sorted([{"time": iso(m["time"]), "subject": m["subject"]} for m in fg.sent] + monitor_alerts,
                    key=lambda a: a["time"])
    res["alerts"] = alerts
    na = [e for e in emails if e.kind == "no_attachment"]
    if basic:
        res["no_attachment_reported"] = 0
        res["no_attachment"] = len(na)
        return res
    # email filing
    in_inbox = [x for x in fg.messages.values() if x["folder"] == "inbox"]
    res["emails_left_in_inbox"] = len(in_inbox)
    review = {x["id"] for x in fg.messages.values() if fg.folders.get(x["folder"]) == s.review_folder}
    res["no_attachment"] = len(na)
    res["no_attachment_to_review"] = sum(1 for e in na if msg_of[id(e)] in review)
    # register and audit
    reg = fg.lists[s.register_list]["items"].values()
    by_sha = {r["fields"]["ContentSHA256"]: r["fields"] for r in reg}
    res["register_rows"] = len(by_sha)
    res["register_saved"] = sum(1 for f in by_sha.values() if f["Status"] == "Saved")
    audit = [i["fields"] for i in fg.lists[s.audit_list]["items"].values()]
    outcome_events = {"saved", "duplicate_skipped", "no_attachment"}  # one of these per email = complete trail
    has_outcome = {}
    for a in audit:
        if a["Event"] in outcome_events:
            has_outcome.setdefault(a["MessageId"], set()).add(a["Event"])
    res["emails_with_outcome_event"] = sum(1 for e in emails if msg_of[id(e)] in has_outcome)
    res["emails_filed_event"] = len({a["MessageId"] for a in audit if a["Event"] == "email_filed"})
    res["audit_events"] = len(audit)
    res["audit_events_by_type"] = _count(a["Event"] for a in audit)
    # re-sends flagged
    rs = [e for e in emails if e.kind == "resend"]
    res["resends"] = len(rs)
    res["resends_flagged"] = sum(1 for e in rs
                                 if by_sha.get(hashlib.sha256(e.attachments[0]["content"]).hexdigest(), {}).get("PossibleResendOf"))
    flagged_total = sum(1 for f in by_sha.values() if f.get("PossibleResendOf"))
    res["resend_flags_total"] = flagged_total
    # latency from first receipt to verified save
    lat, lat_incident = [], []
    windows = [(a, b) for _, a, b in scn.incidents]
    for h, t0 in expected.items():
        f = by_sha.get(h)
        if f and f["Status"] == "Saved":
            m = (parse_iso(f["SavedAt"]) - t0).total_seconds() / 60
            near = any(a - timedelta(minutes=1) <= t0 < b for a, b in windows) or \
                any(t0 < a <= parse_iso(f["SavedAt"]) for a, b in windows)
            (lat_incident if near else lat).append(m)
    res["minutes_to_save_normal"] = _summ(lat)
    res["minutes_to_save_during_incidents"] = _summ(lat_incident)
    # alerts vs incidents
    inc = []
    for label, a, b in scn.incidents:
        hits = [x for x in alerts if a <= parse_iso(x["time"]) <= b + timedelta(hours=1)]
        inc.append({"incident": label, "start": iso(a), "end": iso(b),
                    "duration_min": round((b - a).total_seconds() / 60),
                    "alerted": bool(hits), "minutes_to_first_alert":
                        round((parse_iso(hits[0]["time"]) - a).total_seconds() / 60, 1) if hits else None,
                    "first_alert": hits[0]["subject"] if hits else None})
    res["incidents"] = inc
    expl = [x for x in alerts if "without a fax file" in x["subject"]
            or any(a <= parse_iso(x["time"]) <= b + timedelta(hours=1) for _, a, b in scn.incidents)]
    res["alerts_unexplained"] = [x for x in alerts if x not in expl]
    return res


def _count(it):
    d = {}
    for k in it:
        d[k] = d.get(k, 0) + 1
    return d


def _summ(xs):
    if not xs:
        return None
    xs = sorted(xs)
    q = lambda p: round(xs[min(len(xs) - 1, int(p * len(xs)))], 1)
    return {"n": len(xs), "median": round(statistics.median(xs), 1), "p95": q(0.95), "max": round(xs[-1], 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    scn = sc.build(a.scenario, a.seed, days=a.days)
    out = {"seed": a.seed, "service": run_service(scn, a.seed), "basic_flow": run_basic_flow(scn, a.seed)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    sv, bf = out["service"], out["basic_flow"]
    print(f"{a.scenario} seed {a.seed}: unique {sv['unique_fax_files']} | service missing {sv['missing']} dup {sv['duplicate_files']}"
          f" inbox_left {sv['emails_left_in_inbox']} | basic missing {bf['missing']} dup {bf['duplicate_files']}")


if __name__ == "__main__":
    main()
