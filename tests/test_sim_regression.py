"""Regression fixture: three days of fictional traffic with everyday faults, fixed seed."""
from sim import scenario as sc
from sim.run import run_basic_flow, run_service


def test_three_day_everyday_run():
    scn = sc.build("everyday", seed=3, days=3)
    r = run_service(scn, 3)
    assert r["unique_fax_files"] == 168 and r["emails"] == 181
    assert r["missing"] == 0 and r["duplicate_files"] == 0 and r["altered_or_unknown"] == 0
    assert r["emails_left_in_inbox"] == 0 and r["audit_outbox_left"] == 0
    assert r["emails_with_outcome_event"] == r["emails"]
    b = run_basic_flow(scn, 3)
    assert b["duplicate_files"] > 0
