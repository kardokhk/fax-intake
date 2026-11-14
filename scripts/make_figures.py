"""Architecture diagram and test-bench results figure.

usage: python scripts/make_figures.py runs docs
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

FONT = Path(__file__).resolve().parents[1] / "testpack" / "fonts"
for f in FONT.glob("LiberationSans-*.ttf"):
    matplotlib.font_manager.fontManager.addfont(str(f))
plt.rcParams.update({"font.family": "Liberation Sans", "font.size": 9, "axes.linewidth": 0.6,
                     "xtick.major.width": 0.6, "ytick.major.width": 0.6, "svg.fonttype": "none"})
OI = {"blue": "#0072B2", "orange": "#E69F00", "green": "#009E73", "red": "#D55E00", "grey": "#666666",
      "sky": "#56B4E9", "purple": "#CC79A7"}


def box(ax, x, y, w, h, title, sub="", color=OI["blue"], fill="#FFFFFF"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.01,rounding_size=0.015",
                                lw=1.1, ec=color, fc=fill))
    ax.text(x + w / 2, y + h - 0.028, title, ha="center", va="top", fontsize=9.5, weight="bold", color="black")
    if sub:
        ax.text(x + w / 2, y + h - 0.068, sub, ha="center", va="top", fontsize=7.6, color="#222222", linespacing=1.35)


def arrow(ax, a, b, label="", color="#333333", style="-|>", ls="-", lx=0, ly=0.012, rad=0.0):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle=style, mutation_scale=11, lw=1.0, color=color, ls=ls,
                                 connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((a[0] + b[0]) / 2 + lx, (a[1] + b[1]) / 2 + ly, label, ha="center", va="bottom", fontsize=7.4,
                color=color)


def architecture(out: Path) -> None:
    fig = plt.figure(figsize=(7.2, 4.3), dpi=300)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0.02, 0.975, "Fax intake: from RingCentral email to SharePoint, once, with a record of every step",
            fontsize=10.5, weight="bold", va="top")
    box(ax, 0.02, 0.64, 0.17, 0.2, "RingCentral", "inbound fax\nemailed with\nPDF attached", OI["grey"])
    box(ax, 0.25, 0.60, 0.2, 0.26, "Outlook shared\nmailbox", "\nInbox: new fax emails\nFaxes - Saved\nFaxes - Needs Review",
        OI["blue"])
    box(ax, 0.52, 0.55, 0.22, 0.34, "Intake run", "every 5 minutes\n\n1 claim (unique key)\n2 store, never overwrite\n"
        "3 verify size and hash\n4 record as Saved\n5 file the email", OI["green"], "#F2FAF7")
    box(ax, 0.80, 0.68, 0.18, 0.2, "SharePoint library", "Incoming Faxes/YYYY/MM\noriginal file, unchanged\nversioning, retention",
        OI["orange"])
    box(ax, 0.80, 0.38, 0.18, 0.22, "SharePoint lists", "Fax Register\n(one row per fax,\nunique key)\nFax Audit Log",
        OI["orange"])
    box(ax, 0.52, 0.12, 0.22, 0.27, "Checks", "hourly: anything unsaved\nafter 30 min?\nbusiness hours: no fax\nfor 3 h?\n"
        "07:00: summary and\nre-check of yesterday's files", OI["purple"])
    box(ax, 0.80, 0.08, 0.18, 0.22, "Administrators", "alert emails\n(no patient details)\ndaily summary", OI["red"])
    box(ax, 0.25, 0.12, 0.2, 0.22, "External monitor", "heartbeat from each run;\nalerts if no healthy run\nfor 20 minutes", OI["red"])
    arrow(ax, (0.19, 0.74), (0.25, 0.74), "fax email")
    arrow(ax, (0.45, 0.74), (0.52, 0.74), "read")
    arrow(ax, (0.74, 0.79), (0.80, 0.79), "")
    ax.text(0.77, 0.81, "upload", ha="center", va="bottom", fontsize=7.4, color="#333333")
    arrow(ax, (0.74, 0.62), (0.80, 0.52), "")
    ax.text(0.765, 0.535, "claim,\nrecord", ha="right", va="top", fontsize=7.4, color="#333333")
    arrow(ax, (0.52, 0.64), (0.45, 0.66), "move", ly=-0.045)
    arrow(ax, (0.63, 0.55), (0.63, 0.39), "", OI["purple"])
    arrow(ax, (0.74, 0.22), (0.80, 0.20), "", OI["red"])
    arrow(ax, (0.52, 0.58), (0.45, 0.30), "heartbeat", OI["red"], ls="--", lx=-0.03)
    arrow(ax, (0.35, 0.12), (0.80, 0.11), "", OI["red"], rad=0.18)
    ax.text(0.57, 0.075, "alert if silent", ha="center", fontsize=7.4, color=OI["red"])
    ax.text(0.02, 0.918, "Safe to repeat: every run handles only what is still unfinished, so a missed or failed run is "
            "completed by the next one.", fontsize=7.6, color="#333333")
    fig.savefig(out / "architecture.png", dpi=300)
    fig.savefig(out / "architecture.pdf")
    plt.close(fig)


def results(runs: Path, out: Path) -> None:
    s = json.loads((runs / "summary.json").read_text())
    scen = ["clean", "everyday", "outages"]
    names = {"clean": "No faults", "everyday": "Everyday faults", "outages": "Everyday faults\n+ 7 incidents"}
    fig, axs = plt.subplots(1, 2, figsize=(7.2, 3.0), dpi=300, gridspec_kw={"width_ratios": [1, 1.25], "wspace": 0.55})
    ax = axs[0]
    import numpy as np
    x = np.arange(len(scen))
    w = 0.2
    series = [("Basic flow: faxes missing", [s[k]["basic_missing"] for k in scen], OI["red"], "//"),
              ("Basic flow: duplicate files", [s[k]["basic_dup"] for k in scen], OI["orange"], ""),
              ("Service: faxes missing", [s[k]["svc_missing"] for k in scen], OI["blue"], "//"),
              ("Service: duplicate files", [s[k]["svc_dup"] for k in scen], OI["sky"], "")]
    for i, (lab, v, c, h) in enumerate(series):
        bars = ax.bar(x + (i - 1.5) * w, v, w, label=lab, color=c, hatch=h, edgecolor="black", lw=0.4)
        for b, val in zip(bars, v):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 6, str(val), ha="center", va="bottom", fontsize=6.5)
    ax.set_xticks(x, [names[k] for k in scen], fontsize=7.5)
    ax.set_ylabel("Files, total over 5 simulated months")
    ax.set_ylim(0, 560)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=6.3, frameon=False, loc="upper left", ncol=2, columnspacing=0.8, handlelength=1.4)
    ax.set_title("a  Faxes lost or stored twice", fontsize=8.5, loc="left", weight="bold")
    ax = axs[1]
    inc = s["outages"]["incidents"]
    lab = {"sharepoint_outage": "SharePoint down", "mail_outage": "Mailbox service down",
           "register_outage": "Register lists down", "host_down_night": "Intake host down (night)",
           "host_down_day": "Intake host down (day)", "notifications_stopped": "Fax emails stop arriving",
           "access_denied": "Access expired (403)"}
    runs_ = [json.loads(p.read_text())["service"] for p in sorted(runs.glob("outages_s*.json"))]
    keys = list(lab)
    for i, k in enumerate(keys):
        d = inc[k]["duration_min"] / 60
        ax.barh(i, d, color="#E8E8E8", edgecolor="#999999", lw=0.4, height=0.6)
        pts = [x["minutes_to_first_alert"] / 60 for r in runs_ for x in r["incidents"] if x["incident"] == k]
        ax.scatter(pts, [i] * len(pts), s=14, color=OI["blue"], zorder=3, edgecolor="black", lw=0.3)
        ax.text(d + 0.15, i, f"{inc[k]['alerted']}/{inc[k]['n']} alerted", va="center", fontsize=6.5)
    ax.set_yticks(range(len(keys)), [lab[k] for k in keys], fontsize=7.5)

    ax.set_xlabel("Hours from incident start")
    ax.set_xlim(0, 13.5)
    ax.set_ylim(len(keys) + 0.6, -0.6)
    ax.spines[["top", "right"]].set_visible(False)
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    ax.legend(handles=[Line2D([], [], marker="o", ls="", ms=4, mfc=OI["blue"], mec="black", mew=0.3,
                              label="first alert (one per simulated month)"),
                       Patch(fc="#E8E8E8", ec="#999999", label="incident duration")],
              fontsize=6.5, frameon=False, loc="lower right", bbox_to_anchor=(1.0, 0.0))
    ax.set_title("b  Time to first alert (service)", fontsize=8.5, loc="left", weight="bold")
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.2, top=0.88)
    fig.savefig(out / "results.png", dpi=300)
    fig.savefig(out / "results.pdf")
    plt.close(fig)


if __name__ == "__main__":
    runs, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    architecture(out)
    results(runs, out)
    print("figures written to", out)
