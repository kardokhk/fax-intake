"""Behaviour of one intake run against the Graph stand-in: the cases the design has to get right."""
import hashlib
import threading
from datetime import timedelta

import httpx

from faxintake.quickxor import quickxorhash
from sim.fakegraph import FaultPlan, Outage

from .helpers import T0, client, events, fax_email, pdf, service, tenant


def stored(fg):
    return {hashlib.sha256(it["_content"]).hexdigest(): p for p, it in fg.files.items()}


def test_saves_once_verifies_and_files_email(tmp_path):
    s, clock, fg = tenant()
    mid = fg.deliver(fax_email(pdf(1)))
    rep = service(s, clock, fg, tmp_path).run()
    assert rep.files_saved == 1 and rep.emails_filed == 1 and rep.healthy
    (path, item), = fg.files.items()
    assert item["_content"] == pdf(1)                                  # byte-identical original
    assert path.startswith("Incoming Faxes/2026/09/2026-09-08_0950_from-5550142231_2p_")
    assert fg.folders[fg.messages[mid]["folder"]] == s.saved_folder     # email kept, moved not deleted
    row, = [i["fields"] for i in fg.lists[s.register_list]["items"].values()]
    assert row["Status"] == "Saved" and row["QuickXorHash"] == quickxorhash(pdf(1))
    assert [e["Event"] for e in events(fg, s)] == ["received", "saved", "email_filed"]


def test_second_run_does_nothing(tmp_path):
    s, clock, fg = tenant()
    fg.deliver(fax_email(pdf(1)))
    service(s, clock, fg, tmp_path).run()
    rep = service(s, clock, fg, tmp_path, run_id="r2").run()
    assert rep.emails_seen == 0 and len(fg.files) == 1


def test_duplicate_delivery_and_second_notification_are_not_stored_twice(tmp_path):
    s, clock, fg = tenant()
    fg.deliver(fax_email(pdf(1), imid="<a@x>"))
    fg.deliver(fax_email(pdf(1), minutes=1, imid="<a@x>"))     # same email delivered twice
    fg.deliver(fax_email(pdf(1), minutes=4, imid="<b@x>"))     # separate notification, same fax file
    rep = service(s, clock, fg, tmp_path).run()
    assert len(fg.files) == 1 and rep.duplicates == 2 and rep.emails_filed == 3
    assert len(events(fg, s, "duplicate_skipped")) == 2
    row, = [i["fields"] for i in fg.lists[s.register_list]["items"].values()]
    assert row["DuplicateCount"] == 2


def test_resend_is_kept_and_flagged(tmp_path):
    s, clock, fg = tenant()
    fg.deliver(fax_email(pdf(1), minutes=0))
    fg.deliver(fax_email(pdf(2), minutes=5))          # same office, same page count, different file
    service(s, clock, fg, tmp_path).run()
    assert len(fg.files) == 2
    rows = sorted((i["fields"] for i in fg.lists[s.register_list]["items"].values()), key=lambda f: f["ReceivedAt"])
    assert not rows[0].get("PossibleResendOf") and rows[1]["PossibleResendOf"] == rows[0]["TargetPath"]


def test_email_without_attachment_goes_to_review_with_alert(tmp_path):
    s, clock, fg = tenant()
    mid = fg.deliver(fax_email(None))
    rep = service(s, clock, fg, tmp_path).run()
    assert fg.folders[fg.messages[mid]["folder"]] == s.review_folder
    assert rep.alerts == 1 and "without a fax file" in fg.sent[0]["subject"]
    assert not fg.files


def test_other_senders_are_ignored(tmp_path):
    s, clock, fg = tenant()
    mid = fg.deliver(fax_email(pdf(1), sender="nurse@agency.example"))
    service(s, clock, fg, tmp_path).run()
    assert not fg.files and fg.messages[mid]["folder"] == "inbox"


def test_lost_responses_never_create_duplicates(tmp_path):
    # every write is committed but its response is lost: worst case for retries
    s, clock, fg = tenant(FaultPlan(p_lost=1.0))
    fg.deliver(fax_email(pdf(1)))
    for k in range(4):
        clock.sleep(20 * 60)
        service(s, clock, fg, tmp_path, run_id=f"r{k}").run()
    fg.faults = FaultPlan()
    clock.sleep(20 * 60)
    service(s, clock, fg, tmp_path, run_id="final").run()
    assert len(fg.files) == 1 and len(fg.lists[s.register_list]["items"]) == 1
    row, = [i["fields"] for i in fg.lists[s.register_list]["items"].values()]
    assert row["Status"] == "Saved"
    assert all(m["folder"] != "inbox" for m in fg.messages.values())


class Crash(BaseException):
    pass


def test_crash_after_upload_is_completed_by_next_run_without_second_copy(tmp_path):
    s, clock, fg = tenant()
    fg.deliver(fax_email(pdf(1)))
    seen = {"put": False}

    def handler(req):
        if seen["put"] and req.method == "PATCH":
            raise Crash()                       # dies after storing the file, before marking it Saved
        if req.method == "PUT":
            seen["put"] = True
        return fg.handle(req)
    try:
        service(s, clock, fg, tmp_path, handler=handler).run()
    except Crash:
        pass
    assert len(fg.files) == 1
    row, = [i["fields"] for i in fg.lists[s.register_list]["items"].values()]
    assert row["Status"] == "Claimed"
    clock.sleep(5 * 60)                          # inside the lease: the next run leaves it alone
    rep = service(s, clock, fg, tmp_path, run_id="r2").run()
    assert rep.deferred == 1 and row["Status"] == "Claimed"
    clock.sleep(15 * 60)                         # lease expired: taken over and completed
    rep = service(s, clock, fg, tmp_path, run_id="r3").run()
    assert rep.files_saved == 1 and len(fg.files) == 1 and row["Status"] == "Saved"
    assert events(fg, s, "claim_taken_over")


def test_storage_outage_alerts_when_overdue_then_recovers(tmp_path):
    s, clock, fg = tenant(FaultPlan(outages=[Outage("files", T0 - timedelta(hours=1), T0 + timedelta(hours=2))]))
    fg.deliver(fax_email(pdf(1)))
    for k in range(6):                           # 30 minutes of failing runs
        rep = service(s, clock, fg, tmp_path, run_id=f"r{k}").run()
        assert not rep.healthy
        clock.sleep(5 * 60)
    assert any("not yet saved" in m["subject"] for m in fg.sent)
    n_alerts = len(fg.sent)
    service(s, clock, fg, tmp_path, run_id="again").run()
    assert len(fg.sent) == n_alerts               # same incident is not re-alerted every run
    clock.set(T0 + timedelta(hours=2, minutes=1))
    rep = service(s, clock, fg, tmp_path, run_id="after").run()
    assert rep.files_saved == 1 and rep.healthy and len(fg.files) == 1


def test_access_denied_is_unhealthy(tmp_path):
    s, clock, fg = tenant(FaultPlan(outages=[Outage("all", T0 - timedelta(hours=1), T0 + timedelta(hours=1), 403)]))
    fg.deliver(fax_email(pdf(1)))
    rep = service(s, clock, fg, tmp_path).run()
    assert not rep.healthy and not rep.listing_ok and not fg.files


def test_audit_rows_survive_a_register_outage(tmp_path):
    s, clock, fg = tenant()
    fg.deliver(fax_email(pdf(1)))
    real = fg.handle

    def handler(req):   # audit list unreachable, everything else fine
        if f"/lists/{s.audit_list}/" in str(req.url).replace("%20", " "):
            return httpx.Response(503, json={"error": {"code": "serviceUnavailable", "message": "x"}})
        return real(req)
    service(s, clock, fg, tmp_path, handler=handler).run()
    assert len(fg.files) == 1 and not events(fg, s)
    assert (tmp_path / "audit_outbox.jsonl").read_text().count("\n") == 3
    service(s, clock, fg, tmp_path, run_id="r2").run()        # flushes the outbox first
    assert [e["Event"] for e in events(fg, s)] == ["received", "saved", "email_filed"]


def test_integrity_mismatch_is_not_marked_saved(tmp_path):
    s, clock, fg = tenant()
    fg.deliver(fax_email(pdf(1)))
    real = fg.handle

    def handler(req):
        r = real(req)
        if req.method == "PUT" and r.status_code == 201:
            body = r.json()
            body["file"]["hashes"]["quickXorHash"] = "corrupted="
            return httpx.Response(201, json=body)
        return r
    rep = service(s, clock, fg, tmp_path, handler=handler).run()
    row, = [i["fields"] for i in fg.lists[s.register_list]["items"].values()]
    assert rep.failed == 1 and row["Status"] == "Failed"
    assert any("does not match" in m["subject"] for m in fg.sent)


def test_parallel_runs_on_the_same_email_store_one_copy(tmp_path):
    # e.g. two triggers fire for one message, or an overlapping scheduled run
    s, clock, fg = tenant()
    fg.deliver(fax_email(pdf(1)))
    reps, errs = [], []

    def go(k):
        try:
            reps.append(service(s, clock, fg, tmp_path / f"w{k}", run_id=f"w{k}").run())
        except Exception as e:   # pragma: no cover
            errs.append(e)
    th = [threading.Thread(target=go, args=(k,)) for k in range(8)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert not errs and len(fg.files) == 1 and len(fg.lists[s.register_list]["items"]) == 1
    assert sum(r.files_saved for r in reps) == 1


def test_silence_alarm_only_in_business_hours(tmp_path):
    s, clock, fg = tenant()
    fi = service(s, clock, fg, tmp_path)
    assert fi.check_silence(T0 + timedelta(hours=2)) is True        # Tuesday 12:00 Chicago, nothing since 09:00
    s2, clock2, fg2 = tenant()
    fi2 = service(s2, clock2, fg2, tmp_path / "b")
    assert fi2.check_silence(T0 + timedelta(hours=12)) is False      # 22:00, outside business hours


def test_daily_summary_detects_changed_file(tmp_path):
    from faxintake.daily import daily_summary
    s, clock, fg = tenant()
    fg.deliver(fax_email(pdf(1)))
    fg.deliver(fax_email(pdf(2), minutes=1, fax_from="5550190007"))
    fi = service(s, clock, fg, tmp_path)
    fi.run()
    out = daily_summary(fi)
    assert out["faxes_saved"] == 2 and out["integrity_problems"] == []
    p = next(iter(fg.files))
    fg.files[p]["file"]["hashes"]["quickXorHash"] = quickxorhash(b"edited")   # someone replaced the file
    out = daily_summary(fi)
    assert out["integrity_problems"] == [f"changed: {p}"]
    assert "ACTION NEEDED" in fg.sent[-1]["subject"]


def test_settings_load_example():
    from pathlib import Path
    from faxintake.config import Settings
    s = Settings.load(Path(__file__).resolve().parents[1] / "config.example.yaml")
    assert s.business_hours == (8, 18) and s.file_types == (".pdf", ".tif", ".tiff")
