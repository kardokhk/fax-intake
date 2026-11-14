"""Replay the fictional test pack (.eml files) through the service and check every case's outcome."""
import csv
import email
import hashlib
from email import policy
from email.utils import parsedate_to_datetime
from pathlib import Path

import pytest

from testpack.make_testpack import build

from .helpers import service, tenant


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    out = tmp_path_factory.mktemp("pack")
    build(out)
    return out


def load(path: Path) -> dict:
    m = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    atts = [{"name": p.get_filename(), "contentType": p.get_content_type(), "content": p.get_payload(decode=True)}
            for p in m.iter_attachments()]
    body = m.get_body(("plain",)).get_content()
    return {"subject": m["Subject"], "from": email.utils.parseaddr(m["From"])[1], "received": parsedate_to_datetime(m["Date"]),
            "body": body, "attachments": atts, "internetMessageId": m["Message-ID"]}


def test_every_case_has_its_expected_outcome(pack, tmp_path):
    s, clock, fg = tenant()
    rows = list(csv.DictReader((pack / "manifest.csv").open()))
    ids = {}
    for r in rows:
        msg = load(pack / r["email"])
        clock.set(msg["received"])
        ids[r["case"]] = fg.deliver(msg)
    clock.sleep(300)
    service(s, clock, fg, tmp_path).run()
    folder = {c: fg.folders[fg.messages[i]["folder"]] for c, i in ids.items()}
    reg = {f["ContentSHA256"]: f for f in (i["fields"] for i in fg.lists[s.register_list]["items"].values())}
    stored = {hashlib.sha256(it["_content"]).hexdigest(): p for p, it in fg.files.items()}
    assert len(fg.files) == 8                          # 01-05, 08, 10, 11
    for r in rows:
        c, exp = r["case"], r["expected"]
        if exp.startswith("saved"):
            h = hashlib.sha256((pack / r["fax_file"]).read_bytes()).hexdigest()
            assert h in stored and folder[c] == s.saved_folder, c
            if "re-send" in exp:
                assert reg[h]["PossibleResendOf"].endswith(".pdf"), c
            if ".tif" in exp:
                assert stored[h].endswith(".tif"), c
        elif exp.startswith("duplicate"):
            assert folder[c] == s.saved_folder, c
        elif exp.startswith("moved to review"):
            assert folder[c] == s.review_folder and any("without a fax file" in m["subject"] for m in fg.sent)
        elif exp.startswith("ignored"):
            assert folder[c] == "Inbox", c
    assert sum(f["DuplicateCount"] for f in reg.values()) == 2
