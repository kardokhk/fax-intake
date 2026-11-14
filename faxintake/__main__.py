"""Command line.

  python -m faxintake run   --config config.yaml   # one sweep; schedule every 5 minutes
  python -m faxintake daily --config config.yaml   # morning summary and integrity check

Settings come from the YAML file; tenant_id, client_id and heartbeat_url from the environment
(FAXINTAKE_TENANT_ID, FAXINTAKE_CLIENT_ID, FAXINTAKE_CLIENT_SECRET, FAXINTAKE_HEARTBEAT_URL).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

import httpx

from .auth import TokenAuth
from .clock import Clock
from .config import Settings
from .daily import daily_summary
from .graph import GraphClient
from .processor import FaxIntake


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="faxintake")
    ap.add_argument("command", choices=["run", "daily"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--state-dir", default=os.environ.get("FAXINTAKE_STATE", "state"))
    a = ap.parse_args(argv)
    s = Settings.load(a.config)
    auth = TokenAuth(os.environ["FAXINTAKE_TENANT_ID"], os.environ["FAXINTAKE_CLIENT_ID"])
    http = httpx.Client(base_url="https://graph.microsoft.com/v1.0", auth=auth, timeout=60)
    clock = Clock()
    fi = FaxIntake(GraphClient(http, clock, s.max_retries, s.backoff_base_s, s.backoff_cap_s), s, clock, a.state_dir)
    if a.command == "daily":
        print(json.dumps(daily_summary(fi), indent=1))
        return 0
    rep = fi.run()
    if clock.now().minute < 5:
        fi.check_silence()
    hb = os.environ.get("FAXINTAKE_HEARTBEAT_URL")   # external monitor, e.g. a dead-man's-switch service
    if hb:
        try:
            httpx.get(hb if rep.healthy else hb.rstrip("/") + "/fail", timeout=10)
        except httpx.HTTPError:
            pass
    print(json.dumps(asdict(rep) | {"healthy": rep.healthy}))
    return 0 if rep.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
