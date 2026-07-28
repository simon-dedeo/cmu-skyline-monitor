"""Step 1: rebuild per-(day,bracket) empirical T maps centred on the PINNED centre.

The previous maps had their ring background fitted at the track guess, which was up to
30 px off. A mis-centred ring lets the spot's own wings into the background samples,
biasing bg low and T high -- i.e. systematic UNDER-correction. Refitting at the pinned
centre removes that.

Also records, per (day,bucket), the median background level and the peak deficit, which
step 2 needs as the depth floor for ticks too dim to self-calibrate.
"""
import os, glob, csv, json, numpy as np, cv2

BR = os.path.expanduser("~/monitor/spot_lab/brackets")
OUT = "/tmp/spotmaps"; os.makedirs(OUT, exist_ok=True)
PIN = json.load(open("/tmp/bigstack/pinned.json"))
WIN, R0, R1, TOP_MASK = 150, 110, 170, 20
LVL_PCT, STRUCT_DROP = 50, 0.35


def load3(p):
    img = cv2.imread(p, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 3 and img.shape[2] > 3:
        img = img[:, :, :3]
    return img


def quad_bg(sub, cx, cy, r0=R0, r1=R1):
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


man = open(f"{OUT}/maps.csv", "w", newline="")
mw = csv.writer(man)
mw.writerow(["day", "bucket", "ch", "n", "x_lo", "y_lo", "cx", "cy",
             "lvl_med", "peak_pct", "noise_pct"])
for day in sorted(PIN):
    cx, cy = PIN[day]["cx"], PIN[day]["cy"]
    y_lo = max(TOP_MASK, int(cy) - WIN); x_lo = max(0, int(cx) - WIN)
    gcx, gcy = cx - x_lo, cy - y_lo
    for bk in ("br0", "br1", "br2"):
        cand = []
        for f in sorted(glob.glob(f"{BR}/{day}_*_{bk}_*.png")):
            img = load3(f)
            if img is None:
                continue
            sub = img[y_lo:int(cy) + WIN, x_lo:int(cx) + WIN, :]
            if sub.size == 0:
                continue
            g = sub.astype(np.float32).mean(axis=2)
            lvl = float(np.median(g))
            if lvl < 60 or (g > 252).mean() > 0.05:
                continue
            ratio = g / np.maximum(cv2.GaussianBlur(g, (0, 0), 60), 1e-3)
            gy2, gx2 = np.mgrid[0:g.shape[0], 0:g.shape[1]]
            outer = np.hypot(gx2 - gcx, gy2 - gcy) > 100
            struct = float(np.std(ratio[outer])) if outer.any() else 9.9
            cand.append((lvl, struct, sub.astype(np.float32)))
        if len(cand) < 12:
            print(f"{day} {bk}: {len(cand)} candidates, skipped", flush=True)
            continue
        lvls = np.array([c[0] for c in cand])
        bright = [c for c in cand if c[0] >= np.percentile(lvls, LVL_PCT)] or cand
        bright.sort(key=lambda t: t[1])
        keep = bright[:max(12, int(len(bright) * (1 - STRUCT_DROP)))]
        for ci, chn in enumerate("bgr"):
            rats = []
            for _, _, sub in keep:
                ch = sub[:, :, ci].astype(np.float64)
                bg = quad_bg(ch, gcx, gcy)
                if bg is None or np.median(bg) < 40:
                    continue
                rats.append(ch / np.maximum(bg, 1e-3))
            if len(rats) < 12:
                continue
            T = np.median(np.stack(rats), axis=0).astype(np.float32)
            np.save(f"{OUT}/{day}_{bk}_{chn}.npy", T)
            S = cv2.GaussianBlur(T, (0, 0), 5)
            gy3, gx3 = np.mgrid[0:S.shape[0], 0:S.shape[1]]
            rr = np.hypot(gx3 - gcx, gy3 - gcy)
            peak = (1 - float(np.percentile(S[rr < 25], 5))) * 100
            noise = float(np.std(T[rr > 110])) * 100 if (rr > 110).any() else 99.0
            lv = float(np.median([k[0] for k in keep]))
            mw.writerow([day, bk, chn, len(rats), x_lo, y_lo, round(cx, 1), round(cy, 1),
                         round(lv, 1), round(peak, 3), round(noise, 4)])
            print(f"{day} {bk} {chn}: n={len(rats):3d} peak={peak:5.2f}% "
                  f"noise={noise:.3f}% snr={peak/max(noise,1e-9):5.1f}", flush=True)
man.close()
json.dump(dict(win=WIN, r0=R0, r1=R1, top_mask=TOP_MASK,
               pinned={d: [PIN[d]["cx"], PIN[d]["cy"]] for d in PIN}),
          open(f"{OUT}/geom.json", "w"), indent=1)
print("-> /tmp/spotmaps")
