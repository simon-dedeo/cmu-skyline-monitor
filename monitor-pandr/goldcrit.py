#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""goldcrit.py — measure sunrise/sunset quality criteria across the whole archive, so the
golden-hour TARGET_ELEV can be chosen from data instead of by eye.

Scans archive/<day>/{HHMM_sky.jpg (5-min, all day), HHMM_gold.jpg (1-min, in-window)} and for
each frame records the criteria below against the sun elevation at that instant.

ROIs, 4K pixel coords, read off a gridded frame (see timelapse_tools/grid.py):
  SKY_HIGH   y 110-760,   x 1280-2560   upper open sky (same sample goldpeak.py uses)
  SKY_HOR    y 700-860,   x 200-3600    LOWEST clear sky band above the rooflines -- this is
                                        where sunrise/sunset colour actually lives
  PILLARS    y 1150-1700, x 1650-1830   the portico columns of the limestone building
  FACADE     y 1100-1650, x 100-1500    same building's long facade (orientation control)
  WALL_R     y 1050-1400, x 2000-3400   the concrete building (different orientation control)

CRITERIA
 (1) fraction of red in sky -- per sky ROI:
       red_frac  share of pixels with hue in [0,25]u[335,360] deg, S>=60, 40<=V<=252
       redness   median (R-B)/(R+B), threshold-free companion so conclusions do not hinge on cuts
 (2) golden colour on the pillars -- measured on the BRIGHTEST 40% of the ROI (the sunlit stone
     faces; the shaded recess between columns would otherwise dominate):
       gold_frac share of those pixels with hue in [15,50] deg (goldpeak's band), S>=60
       hue,sat   median hue (deg) and saturation of those pixels
       model     (p90-p10)/median of ROI luminance = "modelling": raking directional light
                 sculpts the columns, flat overcast does not
 Stone is cream, so it reads mildly warm even at noon. The analysis step therefore also forms
 sat MINUS that day's own solar-noon baseline, isolating golden ILLUMINATION from stone colour.

Writes spot_lab/goldcrit.csv (one row per frame). Read-only w.r.t. production. Fail-soft.
Usage: goldcrit.py [--days N] [--out PATH]
"""
import os, sys, glob, csv, datetime, argparse
import numpy as np, cv2
from concurrent.futures import ProcessPoolExecutor

M = os.path.expanduser("~/monitor")
sys.path.insert(0, M)
import solar, math as _math


def sun_azimuth(t):
    """Solar azimuth in degrees east of north (0=N, 90=E, 180=S, 270=W).
    NOAA-style; only needed to explain which facades can receive direct sun."""
    doy = t.timetuple().tm_yday
    frac = (t.hour + t.minute / 60.0 + t.second / 3600.0)
    g = _math.radians(357.529 + 0.98560028 * (doy - 1))
    decl = _math.radians(23.44) * -_math.cos(_math.radians(360.0 / 365.0 * (doy + 10)))
    # NOAA equation of time, MINUTES. The scale factor is 229.18 (= 4 * 180/pi), not 60:
    # the earlier 60 understated EoT by 3.8x, biasing every azim value by up to ~3-4 deg.
    # Fixed 2026-08-19. (Elevation comes from solar.py and was never affected, so the
    # elevation-band conclusions stand; only the azimuth column and Fig 6 shift slightly.)
    eot = 229.18 * (0.000075 + 0.001868 * _math.cos(g) - 0.032077 * _math.sin(g)
                    - 0.014615 * _math.cos(2 * g) - 0.040849 * _math.sin(2 * g))
    tst = frac * 60.0 + eot + 4.0 * solar.LONGITUDE
    ha = _math.radians(tst / 4.0 - 180.0)
    lat = _math.radians(solar.LATITUDE)
    el = _math.asin(_math.sin(lat) * _math.sin(decl) + _math.cos(lat) * _math.cos(decl) * _math.cos(ha))
    den = _math.cos(lat) * _math.cos(el)
    if abs(den) < 1e-9:
        return None
    c = (_math.sin(decl) - _math.sin(lat) * _math.sin(el)) / den
    az = _math.degrees(_math.acos(max(-1.0, min(1.0, c))))
    return az if _math.sin(ha) < 0 else 360.0 - az

ROIS = {
    "skyhi": (110, 760, 1280, 2560),
    "skyhor": (700, 860, 200, 3600),
    # Sunrise colour is LOCALISED: validated 2026-08-18 on 2026-08-02 10:28Z, where the ENE
    # glow filled the right of the frame while the left stayed deep blue -- a full-width band
    # dilutes it to nothing and the median even reads blue. Split the sky by frame position so
    # the glow is measured where it actually is. Sun azimuth at sunrise here is ~64-75 deg
    # (ENE) and that glow lands in skyE, which fixes the frame's orientation empirically.
    "skyE": (150, 900, 2560, 3800),
    "skyC": (150, 900, 1300, 2560),
    "skyW": (150, 900, 100, 1300),
    "pillar": (1150, 1700, 1650, 1830),
    "facade": (1100, 1650, 100, 1500),
    "wallr": (1050, 1400, 2000, 3400),
}
SKY_ROIS = ("skyhi", "skyhor", "skyE", "skyC", "skyW")
SURF_ROIS = ("pillar", "facade", "wallr")
BRIGHT_FRAC = 0.40            # top-luminance share of a surface ROI = the sunlit faces
S_MIN, V_MIN, V_MAX = 60, 40, 252   # sky gates (S_MIN=60 is goldpeak's, tuned for SKY pixels)
S_MIN_STONE = 25              # limestone in golden light reads S~25-60, never >60: using the
                              # sky's S>=60 gate here returned gold_frac=0 all daylight, and
                              # fired spuriously on near-black pre-dawn frames where HSV of
                              # dark pixels is unstable. Validated 2026-08-18.
V_LIT = 60                    # a surface must actually be LIT to be judged (kills night frames)
V_SKY_MIN = 30                # luminance floor for sky colour: without it (R-B)/(R+B) on a
                              # near-black frame returns +/-1.0 garbage.


def _stats(img):
    out = {}
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    b, g, r = (img[:, :, i].astype(np.float32) for i in range(3))
    H = hsv[:, :, 0].astype(np.float32) * 2.0      # cv2 hue 0-179 -> degrees
    S, V = hsv[:, :, 1].astype(np.float32), hsv[:, :, 2].astype(np.float32)

    for name in SKY_ROIS:
        y0, y1, x0, x1 = ROIS[name]
        hh, ss, vv = H[y0:y1, x0:x1].ravel(), S[y0:y1, x0:x1].ravel(), V[y0:y1, x0:x1].ravel()
        rr, bb = r[y0:y1, x0:x1].ravel(), b[y0:y1, x0:x1].ravel()
        ok = (ss >= S_MIN) & (vv >= V_MIN) & (vv <= V_MAX)
        red = ok & (((hh >= 0) & (hh <= 25)) | (hh >= 335))
        amber = ok & (hh > 25) & (hh <= 50)
        warm = red | amber
        out[name + "_red_frac"] = round(float(red.mean()) * 100, 3)
        out[name + "_amber_frac"] = round(float(amber.mean()) * 100, 3)
        out[name + "_warm_frac"] = round(float(warm.mean()) * 100, 3)
        vis = vv >= V_SKY_MIN                       # only pixels bright enough to have a colour
        out[name + "_n_vis"] = int(vis.sum())
        if vis.sum() > 200:
            out[name + "_redness"] = round(float(np.median(
                (rr[vis] - bb[vis]) / np.maximum(rr[vis] + bb[vis], 1e-6))), 4)
        else:
            out[name + "_redness"] = ""             # unsupported, not zero
        out[name + "_sat"] = round(float(np.median(ss)), 1)
        out[name + "_val"] = round(float(np.median(vv)), 1)

    for name in SURF_ROIS:
        y0, y1, x0, x1 = ROIS[name]
        vv = V[y0:y1, x0:x1].ravel()
        hh, ss = H[y0:y1, x0:x1].ravel(), S[y0:y1, x0:x1].ravel()
        if vv.size < 100:
            continue
        thr = np.quantile(vv, 1.0 - BRIGHT_FRAC)
        lit = (vv >= thr) & (vv >= V_LIT)     # brightest faces AND genuinely lit
        out[name + "_n_lit"] = int(lit.sum())
        out[name + "_lit_frac"] = round(float((vv >= V_LIT).mean()) * 100, 2)
        if lit.sum() < 200:                   # nothing lit: leave blank, never 0 (0 means "lit
            for k in ("_gold_frac", "_hue", "_sat", "_val", "_warm"):   # but not golden")
                out[name + k] = ""
        else:
            hl, sl = hh[lit], ss[lit]
            rl, bl = r[y0:y1, x0:x1].ravel()[lit], b[y0:y1, x0:x1].ravel()[lit]
            gold = (sl >= S_MIN_STONE) & (hl >= 15) & (hl <= 50)
            out[name + "_gold_frac"] = round(float(gold.mean()) * 100, 3)
            out[name + "_hue"] = round(float(np.median(hl)), 1)
            out[name + "_sat"] = round(float(np.median(sl)), 1)
            out[name + "_val"] = round(float(np.median(vv[lit])), 1)
            # warmth in DN, the goldpeak convention (meanR-meanB): works on LOW-saturation
            # stone where any hue/sat threshold is fragile.
            out[name + "_warm"] = round(float(np.median(rl - bl)), 2)
        p10, p90, med = (float(np.quantile(vv, .1)), float(np.quantile(vv, .9)),
                         max(float(np.median(vv)), 1e-6))
        out[name + "_model"] = round((p90 - p10) / med, 3)
    return out


def one(job):
    path, kind = job
    base = os.path.basename(path)
    day = os.path.basename(os.path.dirname(path))
    hhmm = base[:4]
    try:
        t = datetime.datetime.strptime(day + hhmm, "%Y-%m-%d%H%M").replace(
            tzinfo=datetime.timezone.utc)
    except Exception:
        return None
    try:
        elev = solar.sun_elevation(t)
        if elev < -12.0:
            return None                                  # night: nothing to judge
        img = cv2.imread(path)
        if img is None or img.shape[:2] != (2160, 3840):
            return None
        rising = solar.sun_elevation(t + datetime.timedelta(minutes=10)) > elev
        row = {"ts": t.isoformat(), "day": day, "hhmm": hhmm, "kind": kind,
               "elev": round(float(elev), 2),
               "which": "morning" if rising else "evening",
               "azim": (lambda a: round(a, 1) if a is not None else "")(sun_azimuth(t)),
               "bright": round(float(img.mean()), 1)}
        row.update(_stats(img))
        return row
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(M, "spot_lab", "goldcrit.csv"))
    a = ap.parse_args()

    days = sorted(d for d in glob.glob(os.path.join(M, "archive", "20*")) if os.path.isdir(d))
    if a.days:
        days = days[-a.days:]
    jobs = []
    for d in days:
        for p in sorted(glob.glob(os.path.join(d, "[0-9]*_sky.jpg"))):
            jobs.append((p, "sky"))
        for p in sorted(glob.glob(os.path.join(d, "[0-9]*_gold.jpg"))):
            jobs.append((p, "gold"))
    print("scanning %d frames over %d days" % (len(jobs), len(days)), flush=True)

    rows, done = [], 0
    with ProcessPoolExecutor(max_workers=4) as ex:
        for r in ex.map(one, jobs, chunksize=8):
            done += 1
            if r: rows.append(r)
            if done % 500 == 0:
                print("  %d/%d  kept %d" % (done, len(jobs), len(rows)), flush=True)
    if not rows:
        print("no rows"); return 1
    keys, seen = [], set()
    for r in rows:
        for k in r:
            if k not in seen: seen.add(k); keys.append(k)
    rows.sort(key=lambda r: r["ts"])
    with open(a.out, "w") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    print("wrote %s (%d rows)" % (a.out, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
