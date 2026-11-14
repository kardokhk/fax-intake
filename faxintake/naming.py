"""Storage path for a fax. Deterministic, so a retry or a second worker computes the same path.

Example: Incoming Faxes/2026/11/2026-11-14_0932_from-5550142_3p_1a2b3c4d.pdf
Contains no patient information: date and time received, the sending fax number,
the page count and the first 8 characters of the file's SHA-256.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def fax_path(base: str, received_utc: datetime, tz: str, fax_from: str | None, pages: int | None,
             sha256_hex: str, ext: str = ".pdf") -> str:
    t = received_utc.astimezone(ZoneInfo(tz))
    parts = [t.strftime("%Y-%m-%d_%H%M")]
    parts.append(f"from-{fax_from}" if fax_from else "from-unknown")
    if pages:
        parts.append(f"{pages}p")
    parts.append(sha256_hex[:8])
    return f"{base}/{t:%Y}/{t:%m}/{'_'.join(parts)}{ext.lower()}"
