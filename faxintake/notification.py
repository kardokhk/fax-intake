"""Read the fax details a fax notification email carries (sender fax number, page count).

RingCentral does not publish the exact subject or body layout of its fax emails, so this parser
is deliberately lenient: it looks for a phone number and a page count anywhere in the subject or
body, and returns None for anything it cannot find. Nothing downstream depends on these fields
being present; they only make file names and the register easier to read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})\b")
_PAGES = re.compile(r"\b(\d{1,3})\s*(?:pages?|pgs?)\b|\bpages?\s*[:=]?\s*(\d{1,3})\b", re.I)


@dataclass
class FaxInfo:
    fax_from: str | None
    pages: int | None


def parse(subject: str, body: str) -> FaxInfo:
    text = f"{subject}\n{body}"
    m = _PHONE.search(text)
    fax_from = "".join(m.groups()) if m else None
    p = _PAGES.search(text)
    pages = int(p.group(1) or p.group(2)) if p else None
    return FaxInfo(fax_from, pages)


def is_fax_sender(address: str, domains: list[str]) -> bool:
    a = (address or "").lower().strip()
    return any(a.endswith("@" + d) or a.endswith("." + d) for d in domains)
