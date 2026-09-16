#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""goldanalyze.py — aggregate goldcrit.csv into "which sun elevation gives the best
sunrise/sunset", then translate the winning elevation bands back into UTC clock times.

Criteria (see goldcrit.py for how each is measured):
  (1) red in sky     skyhor_red_frac  %of near-horizon sky pixels in the red band
                     skyhor_redness   median (R-B)/(R+B), threshold-free companion
  (2) gold on stone  pillar_gold_frac %of sunlit column pixels in the 15-50deg golden band
                     pillar_warm      median (R-B) DN on the sunlit column faces
                     pillar_warm_exc  the same MINUS that day's solar-noon baseline, which is
                                      what isolates golden ILLUMINATION from cream stone
                     pillar_model     (p90-p10)/median luminance = raking-light "modelling"
"""
import os, sys, csv, math, datetime, statistics as st, collections
M = os.path.expanduser("~/monitor"); sys.path.insert(0, M)
import solar

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(M, "spot_lab", "goldcrit.csv")
rows = list(csv.DictReader(open(SRC)))
def f(r, k):
    v = r.get(k, "")
    try: return float(v)
    except Exception: return None

for r in rows: r["_e"] = f(r, "elev")
rows = [r for r in rows if r["_e"] is not None]
print("rows: %d   days: %d" % (len(rows), len(set(r["day"] for r in rows))))

# --- per-day solar-noon baseline for the stone (high sun = neutral illumination) ---
noon = collections.defaultdict(list)
for r in rows:
    if r["_e"] >= 45 and f(r, "pillar_warm") is not None:
        noon[r["day"]].append(f(r, "pillar_warm"))
base = {d: st.median(v) for d, v in noon.items() if len(v) >= 3}
gb = st.median(list(base.values())) if base else 0.0
print("solar-noon pillar warmth baseline: median %.1f DN across %d days" % (gb, len(base)))
for r in rows:
    w = f(r, "pillar_warm")
    r["_wexc"] = None if w is None else round(w - base.get(r["day"], gb), 2)

def table(which, lo, hi, step=1.0):
    sub = [r for r in rows if r["which"] == which and lo <= r["_e"] < hi]
    print("\n" + "="*104)
    print("%s  (n=%d)" % (which.upper(), len(sub)))
    print("="*104)
    print(" elev band    n | sky red%%  redness | pil gold%% pil_warm  warm_exc  model  lit%%")
    out = []
    e = lo
    while e < hi:
        b = [r for r in sub if e <= r["_e"] < e + step]
        if len(b) >= 4:
            g = lambda k: [f(r, k) for r in b if f(r, k) is not None]
            gx = [r["_wexc"] for r in b if r["_wexc"] is not None]
            med = lambda v: st.median(v) if v else float("nan")
            row = dict(elev=e, n=len(b), red=med(g("skyhor_red_frac")),
                       redness=med(g("skyhor_redness")), gold=med(g("pillar_gold_frac")),
                       warm=med(g("pillar_warm")), wexc=med(gx),
                       model=med(g("pillar_model")), lit=med(g("pillar_lit_frac")))
            out.append(row)
            print(" %+5.1f..%+5.1f %4d | %7.2f %8.3f | %8.2f %8.1f %9.1f %6.2f %5.1f"
                  % (e, e+step, row["n"], row["red"], row["redness"], row["gold"],
                     row["warm"], row["wexc"], row["model"], row["lit"]))
        e += step
    return out

mor = table("morning", -12, 25)
eve = table("evening", -12, 25)

def best(out, key, label, reverse=True, need_lit=0.0):
    c = [r for r in out if not math.isnan(r[key]) and r["lit"] >= need_lit]
    if not c: print("  %s: no data" % label); return None
    c.sort(key=lambda r: r[key], reverse=reverse)
    print("  %-28s peak at elev %+.1f..%+.1f  (%s=%.2f)"
          % (label, c[0]["elev"], c[0]["elev"]+1, key, c[0][key]))
    return c[0]

print("\n" + "#"*104)
print("BEST ELEVATION PER CRITERION")
print("#"*104)
for nm, out in (("MORNING", mor), ("EVENING", eve)):
    print("\n%s:" % nm)
    best(out, "red", "(1) red fraction in sky")
    best(out, "redness", "(1b) continuous sky redness")
    best(out, "wexc", "(2) gold on pillars (excess)", need_lit=5.0)
    best(out, "model", "(2b) raking-light modelling", need_lit=5.0)

# --- elevation band -> UTC, for the first and last date in the archive ---
def utc_for(date_str, lo, hi, morning):
    d = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    hits = []
    for m in range(0, 24*60):
        t = d + datetime.timedelta(minutes=m)
        e = solar.sun_elevation(t)
        rising = solar.sun_elevation(t + datetime.timedelta(minutes=10)) > e
        if lo <= e < hi and rising == morning: hits.append(t)
    if not hits: return None
    return hits[0].strftime("%H:%M"), hits[-1].strftime("%H:%M")

print("\n" + "#"*104)
print("WHAT UTC THOSE ELEVATIONS CORRESPOND TO (band drifts across the season)")
print("#"*104)
days = sorted(set(r["day"] for r in rows))
for label, lo, hi, morning in (("morning  -6..-2", -6, -2, True),
                               ("morning  -2..+2", -2, 2, True),
                               ("morning  +2..+8", 2, 8, True),
                               ("evening  +2..-2", -2, 2, False),
                               ("evening  -2..-6", -6, -2, False),
                               ("evening  -6..-10", -10, -6, False)):
    a = utc_for(days[0], lo, hi, morning); b = utc_for(days[-1], lo, hi, morning)
    print("  %-18s %s -> %s   |   %s -> %s"
          % (label, days[0], "%s-%s" % a if a else "n/a", days[-1], "%s-%s" % b if b else "n/a"))

# --- leaderboards: the actual best individual frames ---
print("\n" + "#"*104)
print("LEADERBOARD - actual best frames in the archive")
print("#"*104)
for which in ("evening", "morning"):
    for key, lab in (("skyhor_red_frac", "reddest sky"), ("skyhor_redness", "warmest sky (cont.)")):
        c = [r for r in rows if r["which"] == which and f(r, key) is not None and -12 <= r["_e"] <= 12]
        c.sort(key=lambda r: -f(r, key))
        print("\n  %s / %s:" % (which, lab))
        for r in c[:6]:
            print("    %s UTC  elev %+6.2f  %s=%-7s  redness=%-8s bright=%s"
                  % (r["ts"][:16], r["_e"], key, r.get(key), r.get("skyhor_redness"), r.get("bright")))
    c = [r for r in rows if r["which"] == which and r["_wexc"] is not None
         and (f(r, "pillar_lit_frac") or 0) >= 5 and -12 <= r["_e"] <= 20]
    c.sort(key=lambda r: -r["_wexc"])
    print("\n  %s / most golden pillars (warmth excess over noon):" % which)
    for r in c[:6]:
        print("    %s UTC  elev %+6.2f  warm_exc=%-7s hue=%-6s sat=%-6s model=%s"
              % (r["ts"][:16], r["_e"], r["_wexc"], r.get("pillar_hue"),
                 r.get("pillar_sat"), r.get("pillar_model")))
