#!/usr/bin/env python3
"""lockprobe.py — replay archived brackets through flatfield's TEMPLATE LOCK and dump
candidate gate statistics, so a threshold can be chosen from data instead of guessed.

Reproduces the exact geometry of flatfield._correct()'s lock block (window, dmap, tpl,
+/-25 px search) and for each frame records:
  pk              peak of the TM_CCOEFF_NORMED surface
  m8/m12/m16/m20  CURRENT-style margin: pk - best value outside a +/-w box   (production w=8)
  L8..L20         PROPOSED margin: pk - best rival LOCAL MAXIMUM  >w px away
  psr             (pk - mean_outside)/std_outside, +/-8 mask
  curv            peak sharpness: -(d2/dx2 + d2/dy2) of the 3x3 neighbourhood
  lx,ly           offset the lock would apply
Only replays frames NEWER than the live model's `updated` stamp, so the published model is
exactly the one that was live -- the velocity extrapolation is then faithful.
"""
import os, sys, glob, json, csv, datetime, numpy as np, cv2
sys.path.insert(0, os.path.expanduser("~/monitor"))
import flatfield as ff

BR = os.path.expanduser("~/monitor/spot_lab/brackets")
OUT = "/tmp/lockprobe.csv"

def local_max_rival(cc, loc, w):
    """best rival LOCAL MAXIMUM strictly further than w px from the peak (Chebyshev)."""
    k = 2 * w + 1
    dil = cv2.dilate(cc, np.ones((k, k), np.uint8))
    ismax = (cc >= dil - 1e-9)
    ys, xs = np.where(ismax)
    best = -1.0
    for y, x in zip(ys, xs):
        if max(abs(int(y) - loc[1]), abs(int(x) - loc[0])) <= w:
            continue
        v = float(cc[y, x])
        if v > best: best = v
    return best

def main():
    tm = ff._load_tmap()
    if tm is None:
        print("no tmap"); return 1
    T0, x0, y0, h, sc, mm = tm
    model_updated = datetime.datetime.fromisoformat(mm["updated"])
    fe = datetime.datetime.fromisoformat(mm["fit_epoch"])
    vel = mm["velocity"]
    tmm = mm.get("tmap", {})
    rx0 = float(tmm.get("mx", tmm.get("bx", h)))
    ry0 = float(tmm.get("my", tmm.get("by", h)))
    print("model updated %s  fit_epoch %s  vel %s" % (model_updated, fe, vel))

    files = sorted(glob.glob(os.path.join(BR, "*_br*.png")))
    rows = []
    for p in files:
        base = os.path.basename(p)
        stamp, brs = base[:15], base.split("_br")[1][0]
        try:
            t = datetime.datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(
                tzinfo=datetime.timezone.utc)
        except Exception:
            continue
        if t <= model_updated:          # only frames the live model actually served
            continue
        img = cv2.imread(p)
        if img is None: continue
        H, W = img.shape[:2]
        out = img.astype(np.float32)
        if not (y0 + 2*h <= H and x0 + 2*h <= W): continue

        # --- faithful velocity extrapolation, using the FRAME's time ---
        T = T0.copy(); rx, ry = rx0, ry0
        dt = (t - fe).total_seconds() / 86400.0
        dt = min(max(dt, -0.5), 3.5)
        sp0 = (mm.get("spots") or [{}])[0]
        px = float(sp0.get("fx", 0)) * W + float(vel[0]) * dt
        py = float(sp0.get("fy", 0)) * H + float(vel[1]) * dt
        sxp, syp = px - (x0 + rx), py - (y0 + ry)
        mag = (sxp*sxp + syp*syp) ** 0.5
        if mag > 40.0: sxp, syp = sxp*40.0/mag, syp*40.0/mag
        if abs(sxp) > 0.5 or abs(syp) > 0.5:
            M2 = np.float32([[1, 0, sxp], [0, 1, syp]])
            T = np.stack([cv2.warpAffine(T[:, :, c], M2, (2*h, 2*h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=1.0) for c in range(3)], axis=2)
            rx, ry = rx + sxp, ry + syp

        win = out[y0:y0+2*h, x0:x0+2*h]
        gray = ff._srgb_lin(win.mean(axis=2).astype(np.float32))
        bgs = cv2.GaussianBlur(gray, (0, 0), 45)
        dmap = cv2.GaussianBlur(np.clip(1.0 - gray/np.maximum(bgs, 1e-6), -0.1, 0.3)
                                .astype(np.float32), (0, 0), 4)
        tpl0 = cv2.GaussianBlur((1.0 - T.mean(axis=2)).astype(np.float32), (0, 0), 4)
        ty0, ty1 = int(max(ry-60, 0)), int(min(ry+60, 2*h))
        tx0, tx1 = int(max(rx-60, 0)), int(min(rx+60, 2*h))
        tpl = tpl0[ty0:ty1, tx0:tx1]
        sy0, sy1 = max(ty0-25, 0), min(ty1+25, 2*h)
        sx0, sx1 = max(tx0-25, 0), min(tx1+25, 2*h)
        if tpl.size == 0: continue
        cc = cv2.matchTemplate(dmap[sy0:sy1, sx0:sx1], tpl, cv2.TM_CCOEFF_NORMED)
        if cc.size == 0: continue
        _, pk, _, loc = cv2.minMaxLoc(cc)

        r = {"ts": t.isoformat(), "br": brs, "pk": round(float(pk), 4),
             "ccw": cc.shape[1], "cch": cc.shape[0],
             "lx": (sx0 + loc[0]) - tx0, "ly": (sy0 + loc[1]) - ty0,
             "level": round(float(np.median(win)), 1)}
        for w in (8, 12, 16, 20):
            c2 = cc.copy()
            my0, mx0 = max(0, loc[1]-w), max(0, loc[0]-w)
            c2[my0:loc[1]+w+1, mx0:loc[0]+w+1] = -1.0
            r["m%d" % w] = round(float(pk - (c2.max() if c2.size else -1.0)), 4)
            r["L%d" % w] = round(float(pk - local_max_rival(cc, loc, w)), 4)
        c8 = cc.copy()
        my0, mx0 = max(0, loc[1]-8), max(0, loc[0]-8)
        mask = np.ones(cc.shape, bool); mask[my0:loc[1]+9, mx0:loc[0]+9] = False
        outv = cc[mask]
        r["psr"] = round(float((pk - outv.mean())/(outv.std()+1e-9)), 3) if outv.size else 0.0
        yq, xq = loc[1], loc[0]
        if 0 < yq < cc.shape[0]-1 and 0 < xq < cc.shape[1]-1:
            r["curv"] = round(float(-((cc[yq,xq+1]-2*pk+cc[yq,xq-1]) +
                                      (cc[yq+1,xq]-2*pk+cc[yq-1,xq]))), 4)
        else:
            r["curv"] = 0.0
        rows.append(r)

    if not rows:
        print("no frames newer than model_updated"); return 1
    keys = list(rows[0].keys())
    with open(OUT, "w") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(rows)
    print("wrote %s  (%d frames)" % (OUT, len(rows)))
    return 0

if __name__ == "__main__":
    sys.exit(main())
