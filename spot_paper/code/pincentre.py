"""ONE centre per day, pinned across all three brackets.

The blemish has a single position -- the brackets are the same instant through the same
glass -- so estimating it three times independently throws away the constraint. Depths
differ by exposure (tone curve), so each bracket's dip map is normalised by its own peak
before combining; only then is the shape comparable.

Also uses a half-depth CENTROID rather than argmin: argmin follows single-pixel noise and
any residual background tilt, a centroid averages over the whole footprint.
"""
import csv, json, numpy as np, cv2, os

B = "/tmp/bigstack"
rows = list(csv.DictReader(open(f"{B}/big.csv")))
TRACK = {"20260720": (1183, 172), "20260721": (1192, 161), "20260722": (1214, 162),
         "20260723": (1247, 148), "20260724": (1270, 145), "20260725": (1283, 139),
         "20260726": (1289, 133), "20260727": (1298, 133)}
TOL = 60

def centroid(dip, x_lo, y_lo, tx, ty, tol=TOL):
    """Half-depth centroid within tol of the track guess."""
    S = cv2.GaussianBlur(dip.astype(np.float32), (0, 0), 5)
    gy, gx = np.mgrid[0:S.shape[0], 0:S.shape[1]]
    near = np.hypot(gx + x_lo - tx, gy + y_lo - ty) <= tol
    if not near.any():
        return None
    pk = float(np.percentile(S[near], 99.5))
    m = near & (S >= 0.5 * pk) & (S > 0)
    if m.sum() < 30:
        return None
    w = S[m]
    return (float((gx[m] * w).sum() / w.sum()) + x_lo,
            float((gy[m] * w).sum() / w.sum()) + y_lo, pk, int(m.sum()))

by_day = {}
for r in rows:
    by_day.setdefault(r["day"], []).append(r)

print(f"{'day':10s} {'n_bk':>4s} {'PINNED centre':>16s} {'peak':>6s} "
      f"{'per-bracket centroid spread':>30s}")
out = {}
for day in sorted(by_day):
    tx, ty = TRACK[day]
    norm, singles = [], []
    x_lo = y_lo = None
    for r in by_day[day]:
        p = f"{B}/{day}_{r['bucket']}_T.npy"
        if not os.path.exists(p):
            continue
        T = np.load(p).astype(np.float64)
        xl, yl = int(r["x_lo"]), int(r["y_lo"])
        if x_lo is None:
            x_lo, y_lo, shape = xl, yl, T.shape
        if (xl, yl) != (x_lo, y_lo) or T.shape != shape:
            continue
        dip = 1.0 - T
        c = centroid(dip, xl, yl, tx, ty)
        if c is None:
            continue
        singles.append((r["bucket"], c[0], c[1]))
        norm.append(dip / max(c[2], 1e-9))      # scale out the exposure-dependent depth
    if len(norm) < 2:
        print(f"{day:10s} {len(norm):4d}  (too few brackets)"); continue
    Dc = np.mean(np.stack(norm), axis=0)
    c = centroid(Dc, x_lo, y_lo, tx, ty)
    sx = [s[1] for s in singles]; sy = [s[2] for s in singles]
    spread = f"x {max(sx)-min(sx):5.1f}  y {max(sy)-min(sy):5.1f}"
    print(f"{day:10s} {len(norm):4d}  ({c[0]:7.1f},{c[1]:6.1f}) {c[2]*100:5.2f}%  {spread:>30s}")
    out[day] = dict(cx=round(c[0], 1), cy=round(c[1], 1), npix=c[3],
                    brackets={s[0]: [round(s[1], 1), round(s[2], 1)] for s in singles})
json.dump(out, open(f"{B}/pinned.json", "w"), indent=1)

days = sorted(out)
print("\nday-to-day step of the pinned centre (should be smooth -- the mount settles):")
for a, b in zip(days[:-1], days[1:]):
    d = np.hypot(out[b]["cx"] - out[a]["cx"], out[b]["cy"] - out[a]["cy"])
    print(f"  {a} -> {b}: dx={out[b]['cx']-out[a]['cx']:+6.1f} "
          f"dy={out[b]['cy']-out[a]['cy']:+6.1f}  |step|={d:5.1f} px")
