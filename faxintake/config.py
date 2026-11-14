"""Settings. Every value an administrator may need to change lives here, not in code."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Settings:
    mailbox: str = "faxes@agency.example"              # shared mailbox that receives fax emails
    source_folder: str = "Inbox"                        # where fax emails arrive (or a folder filled by a rule)
    saved_folder: str = "Faxes - Saved"                 # emails are moved here once every PDF is stored
    review_folder: str = "Faxes - Needs Review"         # emails a person must look at (e.g. no PDF attached)
    sender_domains: list[str] = field(default_factory=lambda: ["ringcentral.com"])  # exact sender address is not documented
    file_types: tuple[str, ...] = (".pdf", ".tif", ".tiff")   # RingCentral attaches PDF; TIFF reported occasionally
    site_id: str = "site-intake"
    drive_id: str = "drive-intake"
    base_path: str = "Incoming Faxes"
    register_list: str = "Fax Register"
    audit_list: str = "Fax Audit Log"
    alert_to: list[str] = field(default_factory=lambda: ["it-alerts@agency.example"])
    timezone: str = "America/Chicago"
    lookback_days: int = 14                             # sweep window; covers any outage shorter than this
    claim_lease_minutes: int = 15                       # a claim older than this is taken over
    overdue_minutes: int = 30                           # alert if a fax email is still unsaved after this
    alert_repeat_hours: int = 4                         # the same incident is not re-alerted within this window
    silence_hours: float = 3.0                          # business-hours gap that triggers "no faxes received"
    business_hours: tuple[int, int] = (8, 18)
    resend_window_hours: float = 3.0                    # same sender + same page count inside this = possible re-send
    max_retries: int = 6
    backoff_base_s: float = 2.0
    backoff_cap_s: float = 120.0

    @classmethod
    def load(cls, path: str | Path) -> "Settings":
        data = yaml.safe_load(Path(path).read_text()) or {}
        for k in ("business_hours", "file_types"):
            if k in data:
                data[k] = tuple(data[k])
        return cls(**data)
