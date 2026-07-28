"""Dipole detector: signed half-disk asymmetry along the motion direction. A correction
landing BEHIND the blemish leaves the leading half dark (positive dip) and the trailing
half over-brightened (negative) -- Simon's bright-arc-lower-left / dark-arc-upper-right.
Asymmetry ~ 0 means centred. Averages several frames to beat cloud noise."""
import glob, os, sys, json, datetime, numpy as np, cv2

mm = json.load(open(os.path.expanduser("~/monitor/spot_model.json")))
sp = mm["spots"][0]; vel = mm.get("velocity", [0, 0])
fe = datetime.datetime.fromisoformat(mm["fit_epoch"])
vhat = np.array([vel[0], vel[1]], float)
vhat /= max(np.hypot(*vhat), 1e-9)

def asym(path):
    img = cv2.imread(path)
    if img is None: return None
    g = img.astype(np.float32).mean(axis=2)
    ts = datetime.datetime.strptime("2026-07-28 " + os.path.basename(path)[:4], "%Y-%m-%d %H%M")\
         .replace(tzinfo=datetime.timezone.utc)
    dt = (ts - fe).total_seconds() / 86400.0
    cx, cy = sp["fx"]*3840 + vel[0]*dt, sp["fy"]*2160 + vel[1]*dt
    y0, y1 = max(20, int(cy-230)), min(2160, int(cy+230))
    x0, x1 = max(0, int(cx-230)), min(3840, int(cx+230))
    sub = g[y0:y1, x0:x1]; gy, gx = np.mgrid[y0:y1, x0:x1]
    rr = np.hypot(gx-cx, gy-cy)
    ring = (rr >= 110) & (rr < 170)
    un, vn = (gx[ring]-cx)/100.0, (gy[ring]-cy)/100.0
    A = np.column_stack([np.ones(un.size), un, vn, un**2, un*vn, vn**2])
    vals = sub[ring].astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
    res = vals - A@coef; mad = np.median(np.abs(res-np.median(res)))+1e-9
    keep = np.abs(res) < 4*1.4826*mad
    if keep.sum() > 150: coef, *_ = np.linalg.lstsq(A[keep], vals[keep], rcond=None)
    proj = (gx-cx)*vhat[0] + (gy-cy)*vhat[1]
    def dip_of(mask):
        un2, vn2 = (gx[mask]-cx)/100.0, (gy[mask]-cy)/100.0
        bg = coef[0]+coef[1]*un2+coef[2]*vn2+coef[3]*un2**2+coef[4]*un2*vn2+coef[5]*vn2**2
        den = float(np.median(bg))
        return (1-float(np.median(sub[mask]))/den)*100 if den > 15 else float("nan")
    lead = (rr < 55) & (proj > 8)
    trail = (rr < 55) & (proj < -8)
    if lead.sum() < 30 or trail.sum() < 30: return None
    return dip_of(lead), dip_of(trail)

groups = {"pre-fix 12:30-14:05Z": [], "post-fix >=14:10Z": []}
for f in sorted(glob.glob(os.path.expanduser("~/monitor/archive/2026-07-28/*_sky.jpg"))):
    hhmm = os.path.basename(f)[:4]
    if "1230" <= hhmm < "1405": groups["pre-fix 12:30-14:05Z"].append(f)
    elif hhmm >= "1410": groups["post-fix >=14:10Z"].append(f)
for name, fs in groups.items():
    la, ta = [], []
    for f in fs:
        r = asym(f)
        if r and all(np.isfinite(r)):
            la.append(r[0]); ta.append(r[1])
    if la:
        print(f"{name}: n={len(la)}  leading(dark if +) {np.median(la):+.2f}%  "
              f"trailing(bright if -) {np.median(ta):+.2f}%  DIPOLE={np.median(la)-np.median(ta):+.2f}%")
    else:
        print(f"{name}: no usable frames yet")
