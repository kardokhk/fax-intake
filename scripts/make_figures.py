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


class Diagram:
    """Boxes with square-cornered geometry (no padding), arrows that start and end exactly on box
    edges, and a layout check that fails if any text crosses a box edge or any line."""

    def __init__(self, ax):
        self.ax, self.boxes, self.labels, self.segments = ax, {}, [], []

    def box(self, key, x0, y0, x1, y1, title, sub, color, fill="#FFFFFF"):
        self.ax.add_patch(FancyBboxPatch((x0, y0), x1 - x0, y1 - y0, boxstyle="round,pad=0,rounding_size=0.012",
                                         lw=1.1, ec=color, fc=fill, zorder=2))
        cx = (x0 + x1) / 2
        t1 = self.ax.text(cx, y1 - 0.025, title, ha="center", va="top", fontsize=8.6, weight="bold", zorder=3)
        t2 = self.ax.text(cx, y1 - 0.025 - 0.036 * (title.count("\n") + 1) - 0.012, sub, ha="center", va="top", fontsize=7.0, color="#222222",
                          linespacing=1.3, zorder=3)
        self.boxes[key] = ((x0, y0, x1, y1), [t1, t2])

    def edge(self, key, side, f=0.5):
        x0, y0, x1, y1 = self.boxes[key][0]
        return {"l": (x0, y0 + f * (y1 - y0)), "r": (x1, y0 + f * (y1 - y0)),
                "b": (x0 + f * (x1 - x0), y0), "t": (x0 + f * (x1 - x0), y1)}[side]

    def path(self, pts, color="#333333", ls="-"):
        """Orthogonal polyline; arrow head on the last segment."""
        for p, q in zip(pts[:-2], pts[1:-1]):
            self.ax.plot([p[0], q[0]], [p[1], q[1]], color=color, lw=1.0, ls=ls, solid_capstyle="butt", zorder=1)
        self.ax.add_patch(FancyArrowPatch(pts[-2], pts[-1], arrowstyle="-|>", mutation_scale=10, lw=1.0,
                                          color=color, ls=ls, shrinkA=0, shrinkB=0, zorder=1))
        self.segments += list(zip(pts[:-1], pts[1:]))

    def label(self, x, y, text, color="#333333", ha="center", va="bottom"):
        self.labels.append(self.ax.text(x, y, text, ha=ha, va=va, fontsize=7.0, color=color, zorder=3))

    def check(self, fig) -> list[str]:
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        inv = self.ax.transData.inverted()

        def bb(t):
            e = t.get_window_extent(r)
            (a0, b0), (a1, b1) = inv.transform([(e.x0, e.y0), (e.x1, e.y1)])
            return a0, b0, a1, b1

        def hit(u, v, m=0.0):
            return not (u[2] + m <= v[0] or v[2] + m <= u[0] or u[3] + m <= v[1] or v[3] + m <= u[1])
        bad = []
        for k, (rect, texts) in self.boxes.items():
            for t in texts:
                x0, y0, x1, y1 = bb(t)
                if x0 < rect[0] + 0.006 or x1 > rect[2] - 0.006 or y0 < rect[1] + 0.006 or y1 > rect[3] - 0.006:
                    bad.append(f"text outside box {k}: {t.get_text()[:25]!r}")
        for t in self.labels:
            u = bb(t)
            for k, (rect, _) in self.boxes.items():
                if hit(u, rect, 0.004):
                    bad.append(f"label {t.get_text()!r} touches box {k}")
            for (p, q) in self.segments:
                seg = (min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1]))
                if hit(u, seg, 0.003):
                    bad.append(f"label {t.get_text()!r} touches a line")
        for (p, q) in self.segments:
            seg = (min(p[0], q[0]) + 1e-3, min(p[1], q[1]) + 1e-3, max(p[0], q[0]) - 1e-3, max(p[1], q[1]) - 1e-3)
            for k, (rect, _) in self.boxes.items():
                if seg[0] <= seg[2] and seg[1] <= seg[3] and hit(seg, rect):
                    bad.append(f"line crosses box {k}")
                elif hit(seg, rect, -0.0005) and seg[0] < rect[2] - 1e-3 and seg[2] > rect[0] + 1e-3 \
                        and seg[1] < rect[3] - 1e-3 and seg[3] > rect[1] + 1e-3:
                    bad.append(f"line crosses box {k}")
        return bad


def architecture(out: Path) -> None:
    fig = plt.figure(figsize=(7.2, 4.6), dpi=300)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0.02, 0.975, "Fax intake: from RingCentral email to SharePoint, once, with a record of every step",
            fontsize=10, weight="bold", va="top")
    ax.text(0.02, 0.928, "Safe to repeat: each run handles only what is still unfinished, so a missed or failed run "
            "is completed by the next one.", fontsize=7.4, color="#333333", va="top")
    d = Diagram(ax)
    # columns: 0.02-0.16 | 0.24-0.42 | 0.52-0.74 | 0.83-0.98
    d.box("rc", 0.02, 0.60, 0.16, 0.82, "RingCentral", "inbound fax,\nemailed with\nthe PDF attached", OI["grey"])
    d.box("mb", 0.24, 0.56, 0.42, 0.84, "Outlook shared\nmailbox", "Inbox: new fax emails\nFaxes - Saved\n"
          "Faxes - Needs Review", OI["blue"])
    d.box("run", 0.52, 0.47, 0.74, 0.86, "Intake run", "every 5 minutes\n\n1  claim (unique key)\n2  store, never overwrite\n"
          "3  verify size and hash\n4  record as Saved\n5  file the email", OI["green"], "#F2FAF7")
    d.box("lib", 0.83, 0.66, 0.98, 0.86, "SharePoint\nlibrary", "Incoming Faxes/\nyear/month\noriginals, unchanged",
          OI["orange"])
    d.box("lst", 0.83, 0.38, 0.98, 0.58, "SharePoint\nlists", "Fax Register\n(one row per fax)\nFax Audit Log",
          OI["orange"])
    d.box("mon", 0.24, 0.10, 0.42, 0.32, "External monitor", "receives a heartbeat\nfrom every run; alerts\n"
          "if no healthy run\nfor 20 minutes", OI["red"])
    d.box("chk", 0.52, 0.08, 0.74, 0.34, "Checks", "hourly: any fax unsaved\nafter 30 minutes?\nbusiness hours: no fax\n"
          "for 3 hours?\n07:00: daily summary and\nre-check of saved files", OI["purple"])
    d.box("adm", 0.83, 0.10, 0.98, 0.30, "Administrators", "alert emails\n(no patient details)\ndaily summary", OI["red"])
    y = 0.71
    d.path([d.edge("rc", "r", (y - 0.60) / 0.22), (0.24, y)]); d.label(0.20, y + 0.012, "fax email")
    d.path([(0.42, 0.77), (0.52, 0.77)]); d.label(0.47, 0.782, "read")
    d.path([(0.52, 0.63), (0.42, 0.63)]); d.label(0.47, 0.618, "move email", va="top")
    d.path([(0.74, 0.76), (0.83, 0.76)]); d.label(0.785, 0.772, "upload")
    d.path([(0.74, 0.52), (0.83, 0.52)]); d.label(0.785, 0.532, "claim,\nrecord")
    d.path([(0.63, 0.47), (0.63, 0.34)], OI["purple"])
    d.path([(0.74, 0.20), (0.83, 0.20)], OI["red"]); d.label(0.785, 0.212, "alerts", OI["red"])
    d.path([(0.56, 0.47), (0.56, 0.41), (0.33, 0.41), (0.33, 0.32)], OI["red"], ls="--")
    d.label(0.445, 0.422, "heartbeat", OI["red"])
    d.path([(0.33, 0.10), (0.33, 0.028), (0.905, 0.028), (0.905, 0.10)], OI["red"])
    d.label(0.62, 0.038, "alert if no healthy run arrives", OI["red"])
    problems = d.check(fig)
    if problems:
        raise SystemExit("architecture layout problems:\n  " + "\n  ".join(problems))
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
