"""Per-day, per-bracket EMPIRICAL transmittance maps from ALL bright frames.

Rationale: per-tick localisation is unreliable (single-frame dip SNR is ~1 on most
ticks), but the camera drifts only ~3 px within a day, so one position per day is
enough -- and stacking every bright frame beats cloud residual down as 1/sqrt(N)
instead of the 50-frame cap that left SNR ~= 1.

Selection: brightness first (the dip is multiplicative, so signal scales with scene
level), then drop the cloudiest tail. Ranking on flatness ALONE selects uniform
overcast, which is the flattest sky there is and has no measurable dip -- that bug
produced stacks with a 0.01% peak.

Outputs per (day,bucket): the median ratio-to-ring-background map (an empirical T map,
1 = clean), the located centre, and an SNR figure.
"""
import os, glob, csv, json, sys, numpy as np, cv2

BR   = os.path.expanduser("~/monitor/spot_lab/brackets")
OUT  = "/tmp/bigstack"
BASE = (1184, 176)
WIN, R0, R1, TOP_MASK = 150, 110, 170, 20
TRACK = {"20260720": (1183, 172), "20260721": (1192, 161), "20260722": (1214, 162),
         "20260723": (1247, 148), "20260724": (1270, 145), "20260725": (1283, 139),
         "20260726": (1289, 133), "20260727": (1298, 133)}
TOL = 60
LVL_PCT, STRUCT_DROP = 50, 0.35     # keep brightest half, then drop cloudiest 35%
os.makedirs(OUT, exist_ok=True)

drift = {}
for r in csv.DictReader(open(os.path.expanduser("~/monitor/drift.csv"))):
    if float(r["resp"]) < 0.03:
        continue
    drift.setdefault(r["ts_utc"][:10].replace("-", ""), []).append((float(r["dx"]), float(r["dy"])))
drift = {k: (float(np.median([a for a, _ in v])), float(np.median([b for _, b in v])))
         for k, v in drift.items()}

def quad_bg_map(sub, cx, cy, r0, r1):
    H, W = sub.shape
    gy, gx = np.mgrid[0:H, 0:W]
    rr = np.hypot(gx - cx, gy - cy)
    m = (rr >= r0) & (rr < r1)
    if m.sum() < 80:
        return None
    ys, xs = np.where(m)
    un, vn = (xs - cx) / 100.0, (ys - cy) / 100.0
    A = np.column_stack([np.ones(un.size), un, vn, un**2, un*vn, vn**2])
    vals = sub[m].astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
    res = vals - A @ coef
    mad = np.median(np.abs(res - np.median(res))) + 1e-9
    keep = np.abs(res) < 4 * 1.4826 * mad
    if keep.sum() > 60:
        coef, *_ = np.linalg.lstsq(A[keep], vals[keep], rcond=None)
    un2, vn2 = (gx - cx) / 100.0, (gy - cy) / 100.0
    Aall = np.column_stack([np.ones(gx.size), un2.ravel(), vn2.ravel(),
                            (un2**2).ravel(), (un2*vn2).ravel(), (vn2**2).ravel()])
    return (Aall @ coef).reshape(H, W)

man = open(f"{OUT}/big.csv", "w", newline="")
mw = csv.writer(man)
mw.writerow(["day","bucket","n","x_lo","y_lo","cx","cy","off_track","peak_pct","noise_pct","snr"])
for day in sorted({os.path.basename(p)[:8] for p in glob.glob(f"{BR}/*.png")}):
    dx, dy = drift.get(day, (0.0, 0.0))
    pcx, pcy = int(round(BASE[0] + dx)), int(round(BASE[1] + dy))
    y_lo = max(TOP_MASK, pcy - WIN); x_lo = max(0, pcx - WIN)
    for bk in ("br0", "br1", "br2"):
        npyp = f"{OUT}/{day}_{bk}_T.npy"
        cand = []
        for f in sorted(glob.glob(f"{BR}/{day}_*_{bk}_*.png")):
            img = cv2.imread(f, cv2.IMREAD_UNCHANGED)
            if img is None:
                continue
            if img.ndim == 3 and img.shape[2] > 3:
                img = img[:, :, :3]
            g = img.astype(np.float32)
            g = g.mean(axis=2) if g.ndim == 3 else g
            sub = g[y_lo:pcy + WIN, x_lo:pcx + WIN]
            if sub.size == 0:
                continue
            lvl = float(np.median(sub))
            if lvl < 60 or (sub > 252).mean() > 0.05:
                continue
            gy2, gx2 = np.mgrid[0:sub.shape[0], 0:sub.shape[1]]
            rr = np.hypot(gx2 + x_lo - pcx, gy2 + y_lo - pcy)
            ratio = sub / np.maximum(cv2.GaussianBlur(sub, (0, 0), 60), 1e-3)
            outer = rr > 100
            struct = float(np.std(ratio[outer])) if outer.any() else 9.9
            cand.append((lvl, struct, sub.astype(np.float32)))
        if len(cand) < 12:
            print(f"{day} {bk}: only {len(cand)} candidates, skipped", flush=True)
            continue
        lvls = np.array([c[0] for c in cand])
        bright = [c for c in cand if c[0] >= np.percentile(lvls, LVL_PCT)] or cand
        bright.sort(key=lambda t: t[1])
        keep = bright[:max(12, int(len(bright) * (1 - STRUCT_DROP)))]
        # ratio-to-ring-background per frame, centred on the day's track guess, then median
        tx, ty = TRACK.get(day, (pcx, pcy))
        gcx, gcy = tx - x_lo, ty - y_lo
        rats = []
        for _, _, sub in keep:
            bg = quad_bg_map(sub, gcx, gcy, R0, R1)
            if bg is None or np.median(bg) < 40:
                continue
            rats.append(sub / np.maximum(bg, 1e-3))
        if len(rats) < 12:
            print(f"{day} {bk}: only {len(rats)} usable after bg fit, skipped", flush=True)
            continue
        T = np.median(np.stack(rats), axis=0).astype(np.float32)
        # locate on the stacked map, constrained to the track
        S = cv2.GaussianBlur(T, (0, 0), 6)
        gy3, gx3 = np.mgrid[0:S.shape[0], 0:S.shape[1]]
        near = np.hypot(gx3 + x_lo - tx, gy3 + y_lo - ty) <= TOL
        srch = np.where(near, S, np.inf)
        iy, ix = np.unravel_index(np.argmin(srch), srch.shape)
        rr2 = np.hypot(gx3 - ix, gy3 - iy)
        peak = (1 - float(S[iy, ix])) * 100
        noise = float(np.std(T[rr2 > 110])) * 100 if (rr2 > 110).any() else 99.0
        np.save(npyp, T)
        cx, cy = int(ix + x_lo), int(iy + y_lo)
        off = float(np.hypot(cx - tx, cy - ty))
        mw.writerow([day, bk, len(rats), x_lo, y_lo, cx, cy, round(off, 1),
                     round(peak, 3), round(noise, 4), round(peak / max(noise, 1e-9), 2)])
        print(f"{day} {bk}: n={len(rats):3d} centre=({cx},{cy}) off_track={off:4.1f} "
              f"peak={peak:5.2f}% noise={noise:.3f}% snr={peak/max(noise,1e-9):5.1f}", flush=True)
man.close()
print("-> /tmp/bigstack")
