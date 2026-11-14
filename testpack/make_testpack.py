"""Build the fictional test pack: fax PDFs that look like received faxes, the notification emails
that carry them (.eml), and a manifest with the expected outcome of each case.

Everything is invented. Patients are named TEST PATIENT, dates of birth are 01/01/1900, fax and
phone numbers are in the 555-0100 to 555-0199 range set aside for fictional use, and every page carries a banner saying it is
fictional test data. The notification email layout is illustrative (RingCentral does not publish
its exact format); detection relies only on the sender domain and the attachment.

usage: python testpack/make_testpack.py testpack/out
"""
from __future__ import annotations

import csv
import io
import random
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
FONT = {k: str(HERE / "fonts" / v) for k, v in
        {"sans": "LiberationSans-Regular.ttf", "bold": "LiberationSans-Bold.ttf", "mono": "LiberationMono-Regular.ttf"}.items()}
W, H = 1700, 2200   # US Letter at 200 dpi, a common fine fax resolution
BANNER = "FICTIONAL TEST DATA - NOT A REAL PATIENT"
AGENCY_FAX = "(312) 555-0100"

SENDERS = {
    "riverbend": ("Riverbend Family Medicine", "(312) 555-0142"),
    "northgate": ("Northgate Regional Hospital - Discharge Planning", "(312) 555-0167"),
    "lakeside": ("Lakeside Clinical Laboratory", "(312) 555-0183"),
    "summit": ("Summit Health Plan - Utilization Review", "(312) 555-0139"),
    "oakwood": ("Oakwood Cardiology Associates", "(312) 555-0175"),
}

DOCS = {
    "referral": ("HOME HEALTH REFERRAL", [
        "Patient: TEST PATIENT, Alpha          DOB: 01/01/1900          Sex: F",
        "Address: 100 Example Street, Testville          Phone: (312) 555-0101",
        "Referring physician: Dr. A. Sample, MD          NPI: 0000000000",
        "Primary diagnosis: post-operative care following hip replacement (fictional)",
        "Services requested: skilled nursing, physical therapy, occupational therapy",
        "Requested start of care: within 48 hours of discharge",
        "Homebound status: requires assistance of one person and a walker",
        "Insurance: Example Health Plan          Member ID: TEST-000001",
        "Notes: wound check at each visit; medication reconciliation on first visit.",
    ]),
    "order": ("PHYSICIAN ORDER", [
        "Patient: TEST PATIENT, Bravo          DOB: 01/01/1900",
        "Order: increase skilled nursing visits to 2 per week for 4 weeks",
        "Reason: blood pressure monitoring after medication change (fictional)",
        "Effective date: as signed",
        "Ordering physician: Dr. B. Example, MD",
        "Signature: ______________________          Date: __________",
    ]),
    "poc": ("PLAN OF CARE - SIGNATURE REQUESTED", [
        "Patient: TEST PATIENT, Charlie          DOB: 01/01/1900",
        "Certification period: 60 days (fictional dates)",
        "Disciplines: SN 1w4, 2w4; PT 2w4; HHA 2w8",
        "Goals: independent with home exercise program; wound healed",
        "Medications: see attached list (fictional)",
        "Please sign page 4 and fax back to the agency at " + AGENCY_FAX,
    ]),
    "lab": ("LABORATORY RESULT REPORT", [
        "Patient: TEST PATIENT, Delta          DOB: 01/01/1900",
        "Specimen: blood, venous          Collected: fictional date",
        "Test                     Result      Reference range",
        "Example analyte A        5.1         3.5 - 5.5",
        "Example analyte B        142         135 - 145",
        "Example analyte C        1.4  H      0.6 - 1.2",
        "Results are fictional and for software testing only.",
    ]),
    "auth": ("PRIOR AUTHORIZATION DECISION", [
        "Member: TEST PATIENT, Echo          Member ID: TEST-000005",
        "Request: home health skilled nursing, 8 visits",
        "Decision: APPROVED (fictional)",
        "Authorization number: AUTH-TEST-0005",
        "Valid for visits within the fictional authorization period.",
    ]),
    "meds": ("MEDICATION LIST AND DISCHARGE SUMMARY", [
        "Patient: TEST PATIENT, Foxtrot          DOB: 01/01/1900",
        "Discharge summary (fictional) follows on the next pages.",
        "Medication 1: Example-medication A 10 mg once daily",
        "Medication 2: Example-medication B 5 mg twice daily",
        "Medication 3: Example-medication C as needed",
    ]),
}


def font(kind: str, size: int):
    return ImageFont.truetype(FONT[kind], size)


def render_page(rng: random.Random, sender: str, doc: str, page: int, pages: int, when: datetime) -> Image.Image:
    name, number = SENDERS[sender]
    title, lines = DOCS[doc]
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    header = f"{when:%b-%d-%Y %H:%M}   FROM: {name[:34].upper():34s} {number}   TO: {AGENCY_FAX}   P.{page:03d}/{pages:03d}"
    d.text((40, 18), header, font=font("mono", 25), fill=0)
    d.rectangle((90, 90, W - 90, 150), outline=0, width=3)
    d.text((W // 2, 120), BANNER, font=font("bold", 34), fill=0, anchor="mm")
    y = 220
    d.text((110, y), name, font=font("bold", 44), fill=0)
    y += 70
    d.text((110, y), "Fax " + number + "   |   This is fictional test data for software testing", font=font("sans", 28), fill=0)
    y += 80
    d.line((110, y, W - 110, y), fill=0, width=3)
    y += 50
    if page == 1:
        d.text((110, y), title, font=font("bold", 54), fill=0)
        y += 110
        for ln in lines:
            d.text((110, y), ln, font=font("sans", 34), fill=0)
            y += 64
    else:
        d.text((110, y), f"{title} (continued)", font=font("bold", 44), fill=0)
        y += 100
        filler = ["Additional fictional clinical notes for page %d of %d." % (page, pages)] + \
                 [f"Line {k}: example text used to make the page look like a real document." for k in range(1, 15)]
        for ln in filler:
            d.text((110, y), ln, font=font("sans", 32), fill=0)
            y += 60
    d.text((110, H - 120), f"Page {page} of {pages}", font=font("sans", 28), fill=0)
    d.text((W - 110, H - 120), BANNER, font=font("sans", 24), fill=0, anchor="ra")
    # look like a received fax: slight skew, speckle, scan-line streaks, 1-bit
    im = im.rotate(rng.uniform(-0.8, 0.8), resample=Image.BICUBIC, fillcolor=255, expand=False)
    px = im.load()
    for _ in range(4000):
        px[rng.randrange(W), rng.randrange(H)] = 0
    for _ in range(rng.randint(1, 3)):
        yy = rng.randrange(200, H - 200)
        ImageDraw.Draw(im).line((0, yy, W, yy), fill=rng.randint(150, 210), width=1)
    im = im.filter(ImageFilter.GaussianBlur(0.6))
    return im.point(lambda v: 255 if v > 150 else 0).convert("1")


def fax_file(rng, sender, doc, pages, when, tiff=False) -> bytes:
    ims = [render_page(rng, sender, doc, p, pages, when) for p in range(1, pages + 1)]
    buf = io.BytesIO()
    if tiff:
        ims[0].save(buf, format="TIFF", compression="group4", save_all=True, append_images=ims[1:])
    else:
        ims[0].save(buf, format="PDF", resolution=200, save_all=True, append_images=ims[1:])
    return buf.getvalue()


def notification(sender: str, pages: int, when: datetime, att: bytes | None, att_name: str,
                 from_addr: str = "notify@ringcentral.com", msgid: str | None = None) -> EmailMessage:
    name, number = SENDERS[sender]
    m = EmailMessage()
    m["From"] = f"RingCentral <{from_addr}>" if from_addr.endswith("ringcentral.com") else from_addr
    m["To"] = "faxes@agency.example"
    m["Subject"] = f"New Fax Message from {number} on {when:%m/%d/%Y %I:%M %p}"
    m["Date"] = format_datetime(when)
    m["Message-ID"] = msgid or make_msgid(domain="ringcentral.example")
    m.set_content(f"You have a new fax message.\n\nFrom: {number} ({name})\nReceived: {when:%A, %B %d, %Y at %I:%M %p}\n"
                  f"Pages: {pages}\nTo: Intake (ext. 101)\n\n[Illustrative test email. Fictional data.]\n")
    if att is not None:
        maintype, subtype = ("image", "tiff") if att_name.endswith(".tif") else ("application", "pdf")
        m.add_attachment(att, maintype=maintype, subtype=subtype, filename=att_name)
    return m


def build(out: Path, seed: int = 20261114) -> list[dict]:
    rng = random.Random(seed)
    (out / "faxes").mkdir(parents=True, exist_ok=True)
    (out / "emails").mkdir(parents=True, exist_ok=True)
    t0 = datetime(2026, 11, 16, 14, 5, tzinfo=timezone.utc)      # Monday 08:05 Chicago
    rows, made = [], {}

    def add(case, sender, doc, pages, minutes, expected, *, tiff=False, reuse=None, same_msgid=None,
            no_att=False, from_addr="notify@ringcentral.com"):
        when = t0 + timedelta(minutes=minutes)
        ext = ".tif" if tiff else ".pdf"
        if reuse:
            data, fname = made[reuse]
        elif no_att:
            data, fname = None, ""
        else:
            data = fax_file(rng, sender, doc, pages, when, tiff)
            fname = f"{case}{ext}"
            (out / "faxes" / fname).write_bytes(data)
        made[case] = (data, fname)
        msg = notification(sender, pages, when, data, f"fax_{when:%Y%m%d_%H%M%S}{ext}", from_addr,
                           msgid=rows[[r["case"] for r in rows].index(same_msgid)]["message_id"] if same_msgid else None)
        (out / "emails" / f"{case}.eml").write_bytes(bytes(msg))
        rows.append({"case": case, "email": f"emails/{case}.eml", "fax_file": f"faxes/{fname}" if fname else "",
                     "sender": SENDERS[sender][0], "pages": pages, "received_utc": when.isoformat(),
                     "message_id": msg["Message-ID"], "expected": expected})

    add("01_referral", "northgate", "referral", 3, 0, "saved")
    add("02_physician_order", "riverbend", "order", 1, 7, "saved")
    add("03_plan_of_care", "oakwood", "poc", 4, 15, "saved")
    add("04_lab_result", "lakeside", "lab", 2, 22, "saved")
    add("05_authorization", "summit", "auth", 1, 31, "saved")
    add("06_order_delivered_twice", "riverbend", "order", 1, 7.5, "duplicate skipped (same email delivered twice)",
        reuse="02_physician_order", same_msgid="02_physician_order")
    add("07_poc_second_notification", "oakwood", "poc", 4, 19, "duplicate skipped (second notification, same fax file)",
        reuse="03_plan_of_care")
    add("08_referral_resent", "northgate", "referral", 3, 64, "saved and flagged as possible re-send of 01")
    add("09_no_attachment", "lakeside", "lab", 2, 80, "moved to review folder, alert sent", no_att=True)
    add("10_signed_order_tiff", "riverbend", "order", 1, 95, "saved as .tif", tiff=True)
    add("11_long_discharge_packet", "northgate", "meds", 12, 110, "saved")
    add("12_staff_forward", "riverbend", "order", 1, 130, "ignored (not from the fax service; stays in inbox)",
        reuse="02_physician_order", from_addr="nurse@agency.example")
    with (out / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return rows


if __name__ == "__main__":
    rows = build(Path(sys.argv[1] if len(sys.argv) > 1 else "testpack/out"))
    print(f"{len(rows)} cases written")
