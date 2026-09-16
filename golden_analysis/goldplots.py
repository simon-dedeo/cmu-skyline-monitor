#!/usr/bin/env python3
"""goldplots.py — sunrise/sunset quality vs SUN ELEVATION: aggregate, plot, and rank.

CRITERIA (measured by goldcrit.py; this script aggregates and controls confounds)

 (1) Warm/red colour in the sky. Measured as a FRACTION of sky pixels in a hue band, not a
     mean or median: the glow is spatially localised, so a median over the sky reads BLUE even
     during a vivid sunrise. Bands: red = hue<25 or >335, amber = 25-50, warm = either.
     Sky is split by frame position -- skyE / skyC / skyW -- because the ENE sunrise glow fills
     only the right of this frame.
 (2) Golden light on the stone. On the brightest 40% of each surface ROI (the sunlit faces):
     warmth (R-B, DN) expressed as EXCESS over that day's own solar-noon baseline, since cream
     limestone reads mildly warm even at midday; plus "modelling" (p90-p10)/median luminance,
     which is high when directional raking light sculpts the columns and low under flat light.

TWO CONFOUNDS, BOTH CONTROLLED (this is what makes the numbers mean anything)
 a) Adaptive exposure. Below the horizon the camera applies large gain (61 at elev -12), so
    "colour" there is amplified noise and city skyglow. Validity: sky needs gain<=8 and a
    properly exposed sky; stone needs gain==0.
 b) Artificial light. The courtyard's sodium/LED lamps light the stone at night, which made the
    pillars read 97% "golden" at elev -12. Stone validity also needs the ROI >=85% lit, i.e.
    ambient daylight rather than a few lamp pools.
 Invalid regions are SHADED on every figure, never silently dropped.

STATISTICS. Vivid colour is rare, so the median is not the useful summary. Reported per
elevation bin: median, p90 (how good the good days get), and HIT RATE (% of frames exceeding
1% warm pixels) -- the actionable curve for "when should the scanner be looking".

Palette: dataviz reference slots 1/2/3, validated all-pairs light mode. Aqua is below 3:1 on
this surface, so every series is direct-labelled and the tables print to stdout (relief rule).
"""
import csv, os, sys, math, bisect, datetime, statistics as st, collections
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
SURF, INK, INK2, MUT, SHADE = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8985", "#e8e6e0"
plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": "#d9d7d0", "grid.color": "#eceae4", "grid.linewidth": .8,
    "axes.grid": True, "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 9.5, "axes.titlesize": 11, "figure.dpi": 130, "lines.linewidth": 2.0})

def fl(r, k):
    try: return float(r.get(k, ""))
    except Exception: return None

rows = list(csv.DictReader(open(os.path.join(SP, "goldcrit.csv"))))
sci = list(csv.DictReader(open(os.path.join(SP, "science.csv"))))
def T(s):
    try: return datetime.datetime.fromisoformat(s)
    except Exception: return None
sv = sorted([(T(r["ts_utc"]), r) for r in sci if T(r["ts_utc"])]); sk = [k for k, _ in sv]
def near(t, tol=300):
    i = bisect.bisect_left(sk, t); best, bd = None, 1e9
    for j in (i-1, i, i+1):
        if 0 <= j < len(sv):
            d = abs((sv[j][0]-t).total_seconds())
            if d < bd: best, bd = sv[j][1], d
    return best if bd <= tol else None
for r in rows:
    r["_e"] = fl(r, "elev"); r["_az"] = fl(r, "azim")
    s = near(T(r["ts"])); r["_g"] = fl(s, "ref_gain") if s else None
    r["_cl"] = fl(s, "cloud_frac") if s else None
rows = [r for r in rows if r["_e"] is not None and r["_g"] is not None]

base = {}
for surf in ("pillar", "facade", "wallr"):
    per = collections.defaultdict(list)
    for r in rows:
        if r["_e"] >= 45 and fl(r, surf+"_warm") is not None: per[r["day"]].append(fl(r, surf+"_warm"))
    med = {d: st.median(v) for d, v in per.items() if len(v) >= 3}
    base[surf] = (med, st.median(list(med.values())) if med else 0.0)
def wexc(r, surf):
    w = fl(r, surf+"_warm")
    if w is None: return None
    m, gl = base[surf]; return w - m.get(r["day"], gl)

sky_ok = lambda r: r["_g"] <= 8 and (fl(r, "skyE_val") or 0) >= 40
stone_ok = lambda r: r["_g"] == 0 and (fl(r, "pillar_lit_frac") or 0) >= 85

def prof(which, get, ok, stat="median", lo=-12, hi=26, step=1.0, minn=4):
    xs, ys = [], []
    e = lo
    while e < hi:
        b = [r for r in rows if r["which"] == which and e <= r["_e"] < e+step and ok(r)]
        v = [get(r) for r in b]; v = [x for x in v if x is not None]
        if len(v) >= minn:
            xs.append(e+step/2)
            if stat == "median": ys.append(st.median(v))
            elif stat == "p90": ys.append(sorted(v)[int(.9*(len(v)-1))])
            elif stat == "hit": ys.append(100.0*sum(1 for x in v if x >= 1.0)/len(v))
        e += step
    return xs, ys

def edge(which, ok):
    e = -12.0
    while e < 26:
        if len([r for r in rows if r["which"] == which and e <= r["_e"] < e+1 and ok(r)]) >= 4: return e
        e += 1
    return None
def dress(ax, title, ylab, ed, shl):
    ax.set_title(title, loc="left", color=INK)
    ax.set_xlabel("sun elevation (deg)"); ax.set_ylabel(ylab)
    ax.set_xlim(-12, 26); ax.xaxis.set_major_locator(MultipleLocator(4))
    ax.axvline(0, color=MUT, lw=1.0, ls=(0, (4, 3)), zorder=1)
    if ed is not None:
        ax.axvspan(-12, ed, color=SHADE, zorder=0)
        ax.text(-11.6, ax.get_ylim()[1], shl, va="top", ha="left", fontsize=7.4,
                color=MUT, style="italic")
def tag(ax, xs, ys, t, c):
    """Direct-label each series at ITS OWN PEAK, not at the line end. End-labels collided
    into an illegible stack wherever series converge (they all end near 0 here)."""
    if not xs: return
    i = max(range(len(ys)), key=lambda k: ys[k])
    ax.annotate(t, (xs[i], ys[i]), xytext=(0, 7), textcoords="offset points",
                color=c, fontsize=8.5, ha="center", va="bottom", fontweight="bold")

SKY3 = (("skyE", C1, "east (ENE)"), ("skyC", C2, "centre"), ("skyW", C3, "west"))
SURF3 = (("pillar", C1, "pillars"), ("facade", C2, "facade"), ("wallr", C3, "concrete wall"))

# FIG 1 — p90 warm fraction by sky third
fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.4), sharey=True)
for ax, wh in zip(axes, ("morning", "evening")):
    for k, c, nm in SKY3:
        xs, ys = prof(wh, lambda r, k=k: fl(r, k+"_warm_frac"), sky_ok, "p90")
        ax.plot(xs, ys, color=c, label=nm, marker="o", ms=3.2); tag(ax, xs, ys, nm.split()[0], c)
    dress(ax, wh, "p90 %warm sky pixels (hue<50)", edge(wh, sky_ok), "night boost -\nnot measurable")
axes[0].legend(frameon=False, fontsize=8.5, loc="upper right")
fig.suptitle("Criterion 1 - how vivid the sky gets (p90) vs sun elevation, by sky region",
             x=.008, ha="left", fontsize=12.5, color=INK)
fig.tight_layout(rect=[0, 0, 1, .94]); fig.savefig(SP+"/fig1_sky_warm_p90.png"); plt.close(fig)

# FIG 2 — hit rate
fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.4), sharey=True)
for ax, wh in zip(axes, ("morning", "evening")):
    for k, c, nm in SKY3:
        xs, ys = prof(wh, lambda r, k=k: fl(r, k+"_warm_frac"), sky_ok, "hit")
        ax.plot(xs, ys, color=c, label=nm, marker="o", ms=3.2); tag(ax, xs, ys, nm.split()[0], c)
    dress(ax, wh, "% of frames with >1% warm sky", edge(wh, sky_ok), "night boost -\nnot measurable")
axes[0].legend(frameon=False, fontsize=8.5, loc="upper right")
fig.suptitle("Criterion 1 - how OFTEN warm sky appears vs sun elevation (the actionable curve)",
             x=.008, ha="left", fontsize=12.5, color=INK)
fig.tight_layout(rect=[0, 0, 1, .94]); fig.savefig(SP+"/fig2_sky_hitrate.png"); plt.close(fig)

# FIG 3 — stone warmth excess
fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.4), sharey=True)
for ax, wh in zip(axes, ("morning", "evening")):
    for k, c, nm in SURF3:
        xs, ys = prof(wh, lambda r, k=k: wexc(r, k), stone_ok)
        ax.plot(xs, ys, color=c, label=nm, marker="o", ms=3.2); tag(ax, xs, ys, nm, c)
    ax.axhline(0, color=MUT, lw=1.2)
    dress(ax, wh, "warmth excess over solar noon (R-B, DN)", edge(wh, stone_ok),
          "lamp-lit /\nnight boost")
    ax.text(25.2, 1, "noon baseline", color=MUT, fontsize=7.4, va="bottom", ha="right")
axes[0].legend(frameon=False, fontsize=8.5, loc="lower right")
fig.suptitle("Criterion 2 - golden light on the stone vs sun elevation "
             "(>0 = warmer than midday; it never is)", x=.008, ha="left", fontsize=12.5, color=INK)
fig.tight_layout(rect=[0, 0, 1, .94]); fig.savefig(SP+"/fig3_stone_warmth.png"); plt.close(fig)

# FIG 4 — modelling
fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.4), sharey=True)
for ax, wh in zip(axes, ("morning", "evening")):
    for k, c, nm in SURF3:
        xs, ys = prof(wh, lambda r, k=k: fl(r, k+"_model"), stone_ok)
        ax.plot(xs, ys, color=c, label=nm, marker="o", ms=3.2); tag(ax, xs, ys, nm, c)
    dress(ax, wh, "(p90-p10)/median luminance", edge(wh, stone_ok), "lamp-lit /\nnight boost")
axes[0].legend(frameon=False, fontsize=8.5, loc="upper left")
fig.suptitle("Criterion 2b - raking-light modelling on the stone vs sun elevation",
             x=.008, ha="left", fontsize=12.5, color=INK)
fig.tight_layout(rect=[0, 0, 1, .94]); fig.savefig(SP+"/fig4_modelling.png"); plt.close(fig)

# FIG 5 — validity
fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.2))
ax = axes[0]
for wh, c in (("morning", C1), ("evening", C2)):
    xs, ys = prof(wh, lambda r: r["_g"], lambda r: True)
    ax.plot(xs, ys, color=c, label=wh, marker="o", ms=3.2); tag(ax, xs, ys, wh, c)
ax.set_title("camera gain applied (the confound)", loc="left", color=INK)
ax.set_xlabel("sun elevation (deg)"); ax.set_ylabel("median ref_gain")
ax.set_xlim(-12, 26); ax.xaxis.set_major_locator(MultipleLocator(4))
ax.axvline(0, color=MUT, lw=1, ls=(0, (4, 3))); ax.legend(frameon=False, fontsize=9)
ax = axes[1]
for k, c, nm in (("pillar_lit_frac", C1, "pillar ROI lit %"), ("skyE_val", C2, "east sky exposure")):
    xs, ys = prof("morning", lambda r, k=k: fl(r, k), lambda r: True)
    ax.plot(xs, ys, color=c, label=nm, marker="o", ms=3.2); tag(ax, xs, ys, nm.split()[0], c)
ax.axhline(85, color=MUT, lw=1, ls=(0, (2, 2)))
ax.text(-11.6, 88, "stone-validity threshold", fontsize=7.4, color=MUT, style="italic")
ax.set_title("what is actually illuminated (morning)", loc="left", color=INK)
ax.set_xlabel("sun elevation (deg)"); ax.set_ylabel("% / DN")
ax.set_xlim(-12, 26); ax.xaxis.set_major_locator(MultipleLocator(4))
ax.axvline(0, color=MUT, lw=1, ls=(0, (4, 3))); ax.legend(frameon=False, fontsize=8.5)
fig.suptitle("Fig 5 - validity: below about -3 deg the sun is not the light source",
             x=.008, ha="left", fontsize=12.5, color=INK)
fig.tight_layout(rect=[0, 0, 1, .94]); fig.savefig(SP+"/fig5_validity.png"); plt.close(fig)

# FIG 6 — geometry. TWO panels, because one statistic misleads here: a p90 of "warm"
# is dominated by ordinary daytime amber haze (a broad plateau across midday azimuths) and
# hides the rare vivid events entirely. True RED is what marks sunrise, and it is rare, so
# it needs a MAX. An earlier single-panel version titled "the glow is an ENE phenomenon"
# was contradicted by its own p90 curve; this separates the two signals.
fig, axes = plt.subplots(1, 2, figsize=(11.8, 4.4))
ax = axes[0]
for k, c, nm in SKY3:
    xs, ys = [], []
    for a in range(30, 340, 10):
        b = [r for r in rows if r["_az"] and a <= r["_az"] < a+10 and sky_ok(r)]
        v = [fl(r, k+"_red_frac") for r in b]; v = [x for x in v if x is not None]
        if len(v) >= 6: xs.append(a+5); ys.append(max(v))
    ax.plot(xs, ys, color=c, label=nm, marker="o", ms=3.2); tag(ax, xs, ys, nm.split()[0], c)
ax.set_title("MAX red fraction - the rare vivid events", loc="left", color=INK)
ax.set_ylabel("max %red sky pixels (hue<25)")
ax = axes[1]
for k, c, nm in SKY3:
    xs, ys = [], []
    for a in range(30, 340, 10):
        b = [r for r in rows if r["_az"] and a <= r["_az"] < a+10 and sky_ok(r)]
        v = [fl(r, k+"_amber_frac") for r in b]; v = [x for x in v if x is not None]
        if len(v) >= 6: xs.append(a+5); ys.append(sorted(v)[int(.9*(len(v)-1))])
    ax.plot(xs, ys, color=c, label=nm, marker="o", ms=3.2); tag(ax, xs, ys, nm.split()[0], c)
ax.set_title("p90 amber fraction - ordinary daytime haze", loc="left", color=INK)
ax.set_ylabel("p90 %amber sky pixels (25-50)")
for ax in axes:
    ax.set_xlabel("sun azimuth (deg E of N;  74=sunrise, 141=noon, 291=sunset)")
    for a, lb in ((74, "sunrise"), (291, "sunset")):
        ax.axvline(a, color=MUT, lw=1, ls=(0, (4, 3)))
        ax.text(a+4, ax.get_ylim()[1]*.93, lb, fontsize=7.4, color=MUT, style="italic")
    ax.legend(frameon=False, fontsize=8.5)
fig.suptitle("Fig 6 - RED is a sunrise-azimuth phenomenon; the daytime warmth is amber haze",
             x=.008, ha="left", fontsize=12.5, color=INK)
fig.tight_layout(rect=[0, 0, 1, .94]); fig.savefig(SP+"/fig6_azimuth.png"); plt.close(fig)

# ---------------- ANSWERS ----------------
# Rank each event on the ROI where it actually appears: the camera faces ~north, so the
# ENE sunrise glow lands in the EAST third and the WNW sunset glow in the WEST third.
# (Fig 6 established this; ranking the evening on skyE returned only daytime amber haze.)
ROI_FOR = {"morning": "skyE", "evening": "skyW"}
print("="*100); print("CRITERION 1 - WARM/RED SKY  (morning->skyE, evening->skyW)"); print("="*100)
print(" elev |     MORNING skyE (sunrise)      |     EVENING skyW (sunset)")
print("      |  med    p90   hit%%  red_p90     |  med    p90   hit%%  red_p90")
e = -12.0
while e < 20:
    out = []
    for wh in ("morning", "evening"):
        b = [r for r in rows if r["which"] == wh and e <= r["_e"] < e+1 and sky_ok(r)]
        K = ROI_FOR[wh]
        v = [fl(r, K+"_warm_frac") for r in b]; v = [x for x in v if x is not None]
        rd = [fl(r, K+"_red_frac") for r in b]; rd = [x for x in rd if x is not None]
        if len(v) >= 4:
            out.append("%6.2f %6.2f %5.0f %8.2f" % (
                st.median(v), sorted(v)[int(.9*(len(v)-1))],
                100*sum(1 for x in v if x >= 1.0)/len(v),
                sorted(rd)[int(.9*(len(rd)-1))] if rd else float("nan")))
        else: out.append(" "*29)
    print(" %+5.1f | %s | %s" % (e, out[0], out[1])); e += 1

print("\n" + "="*100); print("BEST FRAMES (valid window only)"); print("="*100)
for wh in ("morning", "evening"):
    K = ROI_FOR[wh]
    c = [r for r in rows if r["which"] == wh and sky_ok(r) and fl(r, K+"_red_frac") is not None]
    c.sort(key=lambda r: -fl(r, K+"_red_frac"))
    print("\n %s - top 8 by RED sky %% in %s:" % (wh, K))
    for r in c[:8]:
        print("   %s UTC  elev %+6.2f  az %5.1f  red %6.2f%%  warm %6.2f%%  amber %5.2f%%"
              % (r["ts"][:16], r["_e"], r["_az"] or -1, fl(r, K+"_red_frac"),
                 fl(r, K+"_warm_frac"), fl(r, K+"_amber_frac")))

for wh in ("morning", "evening"):
    K = ROI_FOR[wh]
    print("\n per-day PEAK RED sky and the elevation it happened at (%s, %s, valid):" % (wh, K))
    per = collections.defaultdict(list)
    for r in rows:
        if r["which"] == wh and sky_ok(r) and fl(r, K+"_red_frac") is not None:
            per[r["day"]].append(r)
    pk = []
    for d, v in sorted(per.items()):
        b = max(v, key=lambda r: fl(r, K+"_red_frac"))
        if fl(b, K+"_red_frac") >= 1.0: pk.append(b)
    for b in sorted(pk, key=lambda r: -fl(r, K+"_red_frac")):
        print("   %s  peak %6.2f%% at %s UTC  elev %+6.2f  az %5.1f" % (
            b["day"], fl(b, K+"_red_frac"), b["ts"][11:16], b["_e"], b["_az"] or -1))
    if pk:
        ev = [b["_e"] for b in pk]
        print("   -> %d of %d %ss exceeded 1%% red sky; peak elevations:"
              " median %+.2f  range %+.2f..%+.2f" % (len(pk), len(per), wh, st.median(ev), min(ev), max(ev)))

print("\n" + "="*100); print("CRITERION 2 - GOLDEN LIGHT ON STONE"); print("="*100)
print(" elev | MORNING pillar_exc facade_exc model | EVENING pillar_exc facade_exc model")
e = -3.0
while e < 26:
    out = []
    for wh in ("morning", "evening"):
        b = [r for r in rows if r["which"] == wh and e <= r["_e"] < e+1 and stone_ok(r)]
        g = lambda f: [x for x in (f(r) for r in b) if x is not None]
        pv, fv, mv = g(lambda r: wexc(r, "pillar")), g(lambda r: wexc(r, "facade")), g(lambda r: fl(r, "pillar_model"))
        if len(pv) >= 4:
            out.append("%11.1f %10.1f %6.2f" % (st.median(pv), st.median(fv) if fv else float("nan"),
                                                st.median(mv) if mv else float("nan")))
        else: out.append(" "*29)
    print(" %+5.1f | %s | %s" % (e, out[0], out[1])); e += 1
mx = [(st.median([wexc(r, "pillar") for r in rows
                  if r["which"] == wh and e <= r["_e"] < e+1 and stone_ok(r)
                  and wexc(r, "pillar") is not None] or [float("nan")]), e, wh)
      for wh in ("morning", "evening") for e in [x*1.0 for x in range(-3, 26)]]
mx = [m for m in mx if not math.isnan(m[0])]
if mx:
    best = max(mx)
    print("\n  pillar warmth NEVER exceeds midday by more than %.1f DN"
          " (best: %s, elev %+.0f..%+.0f)" % (best[0], best[2], best[1], best[1]+1))
print("\nwrote 6 figures to %s" % SP)
