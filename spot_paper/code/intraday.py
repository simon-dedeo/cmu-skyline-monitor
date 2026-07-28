"""Does the blemish move WITHIN a day? Split each day's bright frames into three time
bins, centroid each bin's stack independently (same machinery as the pinned analysis).
If intraday motion is significant, nightly tracking alone can never fully correct, and
the physics points at thermal cycling rather than monotonic mount creep."""
import os, glob, csv, numpy as np, cv2

BR = os.path.expanduser("~/brackets")
PIN = {"20260722": (1225.9, 157.6), "20260723": (1262.0, 143.9), "20260724": (1269.8, 144.1),
       "20260725": (1284.8, 134.4), "20260726": (1288.4, 132.7), "20260727": (1300.0, 132.1)}
WIN, TOP = 150, 20

def centroid(stack, x_lo, y_lo, px, py):
    M = np.median(np.stack(stack), axis=0)
    gcx, gcy = px - x_lo, py - y_lo
    gy, gx = np.mgrid[0:M.shape[0], 0:M.shape[1]]
    rr = np.hypot(gx - gcx, gy - gcy)
    ring = (rr >= 110) & (rr < 150)
    un, vn = (gx[ring]-gcx)/100.0, (gy[ring]-gcy)/100.0
    A = np.column_stack([np.ones(un.size), un, vn, un**2, un*vn, vn**2])
    coef, *_ = np.linalg.lstsq(A, M[ring].astype(np.float64), rcond=None)
    un2, vn2 = (gx-gcx)/100.0, (gy-gcy)/100.0
    bg = (coef[0]+coef[1]*un2+coef[2]*vn2+coef[3]*un2**2+coef[4]*un2*vn2+coef[5]*vn2**2)
    dip = 1.0 - M/np.maximum(bg, 1e-3)
    S = cv2.GaussianBlur(dip.astype(np.float32), (0,0), 5)
    near = rr <= 60
    pk = float(np.percentile(S[near], 99.5))
    if pk <= 0.003: return None
    m = near & (S >= 0.5*pk)
    if m.sum() < 30: return None
    w = S[m].astype(np.float64)
    return (float((gx[m]*w).sum()/w.sum())+x_lo, float((gy[m]*w).sum()/w.sum())+y_lo, pk*100)

print(f"{'day':10s} {'bin':>12s} {'n':>4s} {'cx':>8s} {'cy':>7s} {'peak%':>6s}")
for day, (px, py) in sorted(PIN.items()):
    x_lo, y_lo = max(0, int(px)-WIN), max(TOP, int(py)-WIN)
    frames = []
    for f in sorted(glob.glob(f"{BR}/{day}_*_br1_*.png")):
        hh = int(os.path.basename(f)[9:11])
        img = cv2.imread(f, cv2.IMREAD_UNCHANGED)
        if img is None: continue
        if img.ndim == 3: img = img[:, :, :3]
        sub = img.astype(np.float32).mean(axis=2)[y_lo:int(py)+WIN, x_lo:int(px)+WIN]
        lvl = float(np.median(sub))
        if lvl < 120 or (sub > 252).mean() > 0.02: continue
        frames.append((hh, sub/lvl))
    if len(frames) < 30: 
        print(f"{day:10s}  (only {len(frames)} bright frames)"); continue
    hhs = sorted(set(h for h, _ in frames))
    tercile = max(1, len(frames)//3)
    frames.sort(key=lambda t: t[0])
    for name, chunk in (("early", frames[:tercile]), ("mid", frames[tercile:2*tercile]),
                        ("late", frames[2*tercile:])):
        if len(chunk) < 10: continue
        c = centroid([s for _, s in chunk], x_lo, y_lo, px, py)
        hr = f"{chunk[0][0]:02d}-{chunk[-1][0]:02d}Z"
        if c:
            print(f"{day:10s} {name+' '+hr:>12s} {len(chunk):4d} {c[0]:8.1f} {c[1]:7.1f} {c[2]:6.2f}")
        else:
            print(f"{day:10s} {name+' '+hr:>12s} {len(chunk):4d}  (no dip found)")
