from datetime import datetime, timezone

import pytest

from faxintake import notification
from faxintake.naming import fax_path
from faxintake.quickxor import quickxorhash
import random


def b64url(s):
    return s.replace("+", "-").replace("/", "_")


# reference values computed with rclone v1.68.2 `hashsum quickxor --base64` (URL-safe alphabet)
@pytest.mark.parametrize("data,expected", [
    (b"", "AAAAAAAAAAAAAAAAAAAAAAAAAAA="),
    (b"hello world", "aCgDG9jwBhDc4Q1yawMZAAAAAAA="),
    (random.Random(1).randbytes(1), "IgAAAAAAAAAAAAAAAQAAAAAAAAA="),
    (random.Random(159).randbytes(159), "JztC42hyBUr_6aL9JzPhKYtoYqg="),
    (random.Random(160).randbytes(160), "Pn2um5EmTchQiMYJu-7cmw05TuI="),
    (random.Random(161).randbytes(161), "094JQt73UGYVI8j2VT5bnZtRIaM="),
    (random.Random(300001).randbytes(300001), "kuQnpaNbgHcZEo_M_-DsuAbVDPA="),
])
def test_quickxorhash_matches_reference(data, expected):
    assert b64url(quickxorhash(data)) == expected


def test_parse_finds_number_and_pages():
    i = notification.parse("New Fax Message from (555) 014-2231 on 09/08/2026", "Pages: 3\nTo: Intake")
    assert (i.fax_from, i.pages) == ("5550142231", 3)
    i = notification.parse("Fax received", "You have a new 12 page fax from +1 555.019.0007")
    assert (i.fax_from, i.pages) == ("5550190007", 12)


def test_parse_missing_fields_are_none():
    i = notification.parse("Fax", "no details here")
    assert (i.fax_from, i.pages) == (None, None)


def test_sender_domain_matching():
    assert notification.is_fax_sender("notify@ringcentral.com", ["ringcentral.com"])
    assert notification.is_fax_sender("x@mail.ringcentral.com", ["ringcentral.com"])
    assert not notification.is_fax_sender("x@ringcentral.com.evil.example", ["ringcentral.com"])
    assert not notification.is_fax_sender("nurse@agency.example", ["ringcentral.com"])


def test_fax_path_local_time_and_no_patient_data():
    t = datetime(2026, 11, 14, 15, 32, tzinfo=timezone.utc)
    p = fax_path("Incoming Faxes", t, "America/Chicago", "5550142231", 3, "ab" * 32)
    assert p == "Incoming Faxes/2026/11/2026-11-14_0932_from-5550142231_3p_abababab.pdf"
    assert fax_path("F", t, "America/Chicago", None, None, "cd" * 32, ".TIF").endswith("from-unknown_cdcdcdcd.tif")
