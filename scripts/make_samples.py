"""Replay the fictional test pack through the service (with a SharePoint outage in the middle) and
export what an administrator would see: the Fax Register and Fax Audit Log as Excel, the alert and
summary emails as text, and a picture of sample test faxes.

usage: python scripts/make_samples.py testpack/out docs
"""
from __future__ import annotations

import csv
import email
import random
import sys
import tempfile
from datetime import timedelta
from email import policy
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from faxintake.clock import SimClock  # noqa: E402
from faxintake.config import Settings  # noqa: E402
from faxintake.daily import daily_summary  # noqa: E402
from faxintake.graph import GraphClient  # noqa: E402
from faxintake.processor import FaxIntake  # noqa: E402
from sim.fakegraph import FakeGraph, FaultPlan, Outage  # noqa: E402

FONTS = Path(__file__).resolve().parents[1] / "testpack" / "fonts"


def load(path: Path) -> dict:
    m = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    atts = [{"name": p.get_filename(), "contentType": p.get_content_type(), "content": p.get_payload(decode=True)}
            for p in m.iter_attachments()]
    return {"subject": m["Subject"], "from": email.utils.parseaddr(m["From"])[1],
            "received": parsedate_to_datetime(m["Date"]), "body": m.get_body(("plain",)).get_content(),
            "attachments": atts, "internetMessageId": m["Message-ID"]}


def replay(pack: Path):
    s = Settings()
    rows = list(csv.DictReader((pack / "manifest.csv").open()))
    msgs = sorted(((r, load(pack / r["email"])) for r in rows), key=lambda x: x[1]["received"])
    t0 = msgs[0][1]["received"]
    clock = SimClock(t0)
    # SharePoint unavailable for 50 minutes starting just before the re-sent referral arrives
    out_start = t0 + timedelta(minutes=60)
    fg = FakeGraph(clock, s.mailbox, FaultPlan(outages=[Outage("files", out_start, out_start + timedelta(minutes=50))]), seed=1)
    fg.add_folder(s.saved_folder)
    fg.add_folder(s.review_folder)
    fg.add_list(s.register_list, unique="ContentSHA256")
    fg.add_list(s.audit_list)
    http = httpx.Client(transport=fg.transport(), base_url="https://graph.microsoft.com/v1.0")
    state = Path(tempfile.mkdtemp(prefix="faxsamples-"))
    t, end, k, i = t0, t0 + timedelta(hours=3), 0, 0
    while t <= end:
        while i < len(msgs) and msgs[i][1]["received"] <= t:
            fg.deliver(msgs[i][1])
            i += 1
        clock.set(t)
        fi = FaxIntake(GraphClient(http, clock, max_retries=2, rng=random.Random(k)), s, clock, state, run_id=f"run-{k:04d}")
        fi.run()
        k += 1
        t += timedelta(minutes=5)
    clock.set(t0.replace(hour=13, minute=0) + timedelta(days=1))   # 07:00 next morning, Chicago
    fi = FaxIntake(GraphClient(http, clock), s, clock, state, run_id="daily")
    daily_summary(fi)
    return s, fg


def to_xlsx(s, fg, path: Path) -> None:
    wb = Workbook()
    reg_cols = ["Title", "Status", "ReceivedAt", "SavedAt", "FaxFrom", "Pages", "SizeBytes", "TargetPath",
                "DuplicateCount", "PossibleResendOf", "Attempts", "ContentSHA256", "QuickXorHash", "MessageId",
                "ClaimedBy", "LastError"]
    aud_cols = ["EventTime", "Event", "Detail", "MessageId", "ContentSHA256", "RunId", "IncidentKey"]
    notes = [("Fax Register", "One row per distinct fax file (key: ContentSHA256, enforced unique). Fictional test data."),
             ("Fax Audit Log", "One row per step, append-only. Times are UTC. Fictional test data.")]
    for idx, (name, cols, rows) in enumerate([
            ("Fax Register", reg_cols, [i["fields"] for i in fg.lists[s.register_list]["items"].values()]),
            ("Fax Audit Log", aud_cols, sorted((i["fields"] for i in fg.lists[s.audit_list]["items"].values()),
                                               key=lambda f: f["EventTime"]))]):
        ws = wb.active if idx == 0 else wb.create_sheet()
        ws.title = name
        ws.append([notes[idx][1]])
        ws["A1"].font = Font(italic=True, color="555555")
        ws.append(cols)
        for c in ws[2]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F4E79")
        for r in rows:
            ws.append([r.get(c, "") for c in cols])
        for j, c in enumerate(cols, 1):
            width = max(len(str(c)), *(min(70, len(str(r.get(c, "")))) for r in rows)) + 2
            ws.column_dimensions[get_column_letter(j)].width = min(width, 72)
        for row in ws.iter_rows(min_row=3):
            for c in row:
                c.alignment = Alignment(vertical="top", wrap_text=False)
        ws.freeze_panes = "A3"
        ws.auto_filter.ref = f"A2:{get_column_letter(len(cols))}{ws.max_row}"
    wb.save(path)


def emails_png(fg, path: Path) -> None:
    f = ImageFont.truetype(str(FONTS / "LiberationSans-Regular.ttf"), 26)
    fb = ImageFont.truetype(str(FONTS / "LiberationSans-Bold.ttf"), 28)
    fm = ImageFont.truetype(str(FONTS / "LiberationMono-Regular.ttf"), 23)
    W = 1800
    blocks = []
    for m in fg.sent:
        body = m["body"]["content"]
        lines = []
        for para in body.split("\n"):
            while len(para) > 110:
                cut = para.rfind(" ", 0, 110)
                cut = 110 if cut < 0 else cut
                lines.append(para[:cut])
                para = para[cut + 1:]
            lines.append(para)
        blocks.append((m, lines))
    H = 120 + sum(170 + 32 * len(ls) for _, ls in blocks)
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    d.text((40, 30), "Emails the administrators received during the test-pack replay (fictional data; times UTC)",
           font=fb, fill="black")
    y = 100
    for m, lines in blocks:
        h = 140 + 32 * len(lines)
        d.rounded_rectangle((40, y, W - 40, y + h), radius=14, outline="#1F4E79", width=3)
        d.text((65, y + 18), m["subject"], font=fb, fill="#1F4E79")
        to = ", ".join(r["emailAddress"]["address"] for r in m["toRecipients"])
        d.text((65, y + 60), f"To: {to}    Sent: {m['time']:%Y-%m-%d %H:%M}", font=f, fill="#444444")
        yy = y + 105
        for ln in lines:
            d.text((65, yy), ln, font=fm if "summary" in m["subject"].lower() else f, fill="black")
            yy += 32
        y += h + 30
    im.save(path)


def fax_montage(pack: Path, path: Path) -> None:
    """First pages of four test faxes, side by side, rendered from the PDF image streams."""
    from pypdf import PdfReader
    picks = ["01_referral.pdf", "02_physician_order.pdf", "03_plan_of_care.pdf", "04_lab_result.pdf"]
    pages = []
    for p in picks:
        r = PdfReader(pack / "faxes" / p)
        img = r.pages[0].images[0].image.convert("L")
        pages.append(img.resize((850, 1100)))
    pad, cap = 30, 70
    W = len(pages) * 850 + (len(pages) + 1) * pad
    im = Image.new("RGB", (W, 1100 + 2 * pad + cap + 60), "white")
    d = ImageDraw.Draw(im)
    fb = ImageFont.truetype(str(FONTS / "LiberationSans-Bold.ttf"), 34)
    f = ImageFont.truetype(str(FONTS / "LiberationSans-Regular.ttf"), 28)
    d.text((pad, 20), "Fictional test faxes (first pages): referral, physician order, plan of care, lab result. "
           "All names, numbers and results are invented.", font=fb, fill="black")
    for k, (pg, name) in enumerate(zip(pages, picks)):
        x = pad + k * (850 + pad)
        im.paste(pg.convert("RGB"), (x, 80))
        d.rectangle((x, 80, x + 850, 1180), outline="#888888", width=2)
        d.text((x, 1195), name, font=f, fill="#333333")
    im.save(path)


if __name__ == "__main__":
    pack, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    s, fg = replay(pack)
    to_xlsx(s, fg, out / "register_and_audit_log_sample.xlsx")
    emails_png(fg, out / "alert_and_summary_emails.png")
    (out / "alert_and_summary_emails.txt").write_text(
        "\n\n".join(f"Subject: {m['subject']}\nSent: {m['time']:%Y-%m-%d %H:%M} UTC\n\n{m['body']['content']}" for m in fg.sent))
    fax_montage(pack, out / "test_faxes.png")
    print(len(fg.files), "files stored;", len(fg.sent), "emails sent")
