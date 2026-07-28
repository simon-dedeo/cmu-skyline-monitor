"""Build day-28 per-(bracket,channel) maps + pinned centre into ~/spotmaps so backfill.py
can correct today's frames. Same recipe as the validated per-day maps: bright-then-flat
selection, ratio-to-sigma-clipped-ring-bg, median stack, half-depth centroid pin."""
import os, glob, csv, json, numpy as np, cv2

BR, MAPS = os.path.expanduser("~/brackets"), os.path.expanduser("~/spotmaps")
DAY, PRIOR = "20260728", (1296.0, 131.0)     # spotnight fit + ~0.5d velocity
WIN, R0, R1, TOP = 150, 110, 170, 20

def ring_bg(sub, cx, cy):
    gy, gx = np.mgrid[0:sub.shape[0], 0:sub.shape[1]]
    rr = np.hypot(gx - cx, gy - cy)
    m = (rr >= R0) & (rr < R1)
    if m.sum() < 200: return None
    un, vn = (gx[m]-cx)/100.0, (gy[m]-cy)/100.0
    A = np.column_stack([np.ones(un.size), un, vn, un**2, un*vn, vn**2])
    vals = sub[m].astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
    res = vals - A@coef; mad = np.median(np.abs(res-np.median(res)))*1.4826+1e-9
    keep = np.abs(res) < 3*mad
    if keep.sum() > 150: coef, *_ = np.linalg.lstsq(A[keep], vals[keep], rcond=None)
    un2, vn2 = (gx-cx)/100.0, (gy-cy)/100.0
    return coef[0]+coef[1]*un2+coef[2]*vn2+coef[3]*un2**2+coef[4]*un2*vn2+coef[5]*vn2**2

px, py = PRIOR
y_lo, x_lo = max(TOP, int(py)-WIN), max(0, int(px)-WIN)
gcx, gcy = px-x_lo, py-y_lo
# pin the centre from br0+br1 combined stack first
norm = []
sel = {}
for bk in ("br0", "br1", "br2"):
    cand = []
    for f in sorted(glob.glob(f"{BR}/{DAY}_*_{bk}_*.png")):
        im = cv2.imread(f, cv2.IMREAD_UNCHANGED)
        if im is None: continue
        if im.ndim == 3: im = im[:, :, :3]
        sub = im.astype(np.float32)[y_lo:int(py)+WIN, x_lo:int(px)+WIN]
        g = sub.mean(axis=2)
        lvl = float(np.median(g))
        if lvl < 60 or (g > 252).mean() > 0.05: continue
        ratio = g/np.maximum(cv2.GaussianBlur(g,(0,0),60),1e-3)
        gy2, gx2 = np.mgrid[0:g.shape[0], 0:g.shape[1]]
        outer = np.hypot(gx2-gcx, gy2-gcy) > 100
        cand.append((lvl, float(np.std(ratio[outer])), f, sub))
    if len(cand) < 12:
        print(f"{bk}: only {len(cand)}, skipping bucket"); continue
    lv = np.array([c[0] for c in cand])
    bright = [c for c in cand if c[0] >= np.percentile(lv, 50)] or cand
    bright.sort(key=lambda t: t[1])
    sel[bk] = bright[:max(12, int(len(bright)*0.65))]
    print(f"{bk}: kept {len(sel[bk])}/{len(cand)}")
for bk in ("br0", "br1"):
    if bk not in sel: continue
    rats = []
    for _, _, _, sub in sel[bk]:
        g = sub.mean(axis=2).astype(np.float64)
        bg = ring_bg(g, gcx, gcy)
        if bg is None or np.median(bg) < 40: continue
        rats.append(g/np.maximum(bg, 1e-3))
    if len(rats) >= 12:
        T = np.median(np.stack(rats), axis=0)
        d = 1.0 - T
        S = cv2.GaussianBlur(d.astype(np.float32), (0,0), 5)
        gy2, gx2 = np.mgrid[0:S.shape[0], 0:S.shape[1]]
        near = np.hypot(gx2-gcx, gy2-gcy) <= 60
        pk = float(np.percentile(S[near], 99.5))
        m = near & (S >= 0.5*pk)
        if pk > 0.003 and m.sum() >= 30:
            w = S[m].astype(np.float64)
            norm.append((float((gx2[m]*w).sum()/w.sum())+x_lo, float((gy2[m]*w).sum()/w.sum())+y_lo))
if not norm:
    raise SystemExit("no centre found for day 28")
cx = float(np.mean([c[0] for c in norm])); cy = float(np.mean([c[1] for c in norm]))
print(f"day-28 pinned centre ({cx:.1f},{cy:.1f}) from {len(norm)} buckets")
gcx, gcy = cx-x_lo, cy-y_lo

rows = []
for bk, keep in sel.items():
    for ci, chn in enumerate("bgr"):
        rats = []
        for _, _, _, sub in keep:
            ch = sub[:, :, ci].astype(np.float64)
            bg = ring_bg(ch, gcx, gcy)
            if bg is None or np.median(bg) < 40: continue
            rats.append(ch/np.maximum(bg, 1e-3))
        if len(rats) < 12: continue
        T = np.median(np.stack(rats), axis=0).astype(np.float32)
        S = cv2.GaussianBlur(T, (0,0), 5)
        gy2, gx2 = np.mgrid[0:S.shape[0], 0:S.shape[1]]
        rr = np.hypot(gx2-gcx, gy2-gcy)
        peak = (1 - float(np.percentile(S[rr < 25], 5)))*100
        noise = float(np.std(T[rr > 110]))*100 if (rr > 110).any() else 99.0
        np.save(f"{MAPS}/{DAY}_{bk}_{chn}.npy", T)
        rows.append([DAY, bk, chn, len(rats), x_lo, y_lo, round(cx,1), round(cy,1),
                     0, round(peak,3), round(noise,4)])
        print(f"{bk} {chn}: n={len(rats)} peak={peak:.2f}% noise={noise:.3f}")
with open(f"{MAPS}/maps.csv", "a", newline="") as fh:
    csv.writer(fh).writerows(rows)
g = json.load(open(f"{MAPS}/geom.json"))
g["pinned"][DAY] = [round(cx,1), round(cy,1)]
json.dump(g, open(f"{MAPS}/geom.json", "w"), indent=1)
print("geom.json updated")
