#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""spotlab.py — per-frame window-spot photometry + crops (SPOT.md §2.1–2.3, §3).

  spotlab.py log FRAME ROLE EXP_UNITS GAIN

For the seeded spot + 4 control patches, per channel (B,G,R): disk median/MAD (encoded),
annulus median/MAD, robust 2D-quadratic background fit evaluated under the disk (encoded +
sRGB-linearized), dip_enc/dip_lin, clip fractions, annulus-fit residual MAD (uniformity gate,
recorded not enforced), far-ring median (skirt check; measured on the FULL frame — a 448 crop
cannot contain a 300 px ring), 10 radial-bin medians (spot patch only), sun elevation and the
latest aim-drift reading. One CSV row per patch × channel; 448×448 lossless crops of every
patch for reproducible refits. Fail-soft: any error exits 0 silently (never breaks the tick).
"""
import cv2, numpy as np, os, sys, csv, datetime, glob

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LAB = os.path.join(HERE, "spot_lab")
CSVP = os.path.join(LAB, "spot_photometry.csv")
LINZ = "srgb_v0"                     # linearization version (day-1 hypothesis: inverse sRGB)
# patch registry: the TRACKED spot + 4 fixed controls (SPOT.md §2.3). The spot centre
# comes from spot_model.json (spotnight-published, velocity-extrapolated per tick) --
# the original hardcoded (1163,187) is ~140 px off the blemish by 2026-07-28, so the
# 'spot' photometry had been measuring nearly clean sky.
def _spot_centre():
    try:
        m = __import__("json").load(open(os.path.join(HERE, "spot_model.json")))
        s0 = m["spots"][0]
        cx, cy = float(s0["fx"]) * 3840, float(s0["fy"]) * 2160
        vel, fe = m.get("velocity"), m.get("fit_epoch")
        if vel and fe:
            dt = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.datetime.fromisoformat(fe)).total_seconds() / 86400.0
            dt = min(max(dt, -0.5), 3.5)
            cx, cy = cx + float(vel[0]) * dt, cy + float(vel[1]) * dt
        if 200 < cx < 3600 and 40 < cy < 900:
            return int(round(cx)), int(round(cy))
    except Exception:
        pass
    return 1163, 187


_SCX, _SCY = _spot_centre()
PATCHES = [("spot", _SCX, _SCY), ("c1", 1900, 187), ("c2", 2400, 187),
           ("c3", 2900, 187), ("c4", 3400, 187)]
R_DISK, R_ANN0, R_ANN1 = 60, 110, 160
R_FAR0, R_FAR1 = 220, 300
TOP_MASK = 20                        # exclude top image rows (lens shading / ISP edge zone)
CROP = 448
FIELDS = (["ts_utc", "role", "exp_units", "gain", "linz", "patch", "cx", "cy", "ch",
           "disk_med", "disk_mad", "ann_med", "ann_mad", "bg_fit", "ann_resid_mad",
           "dip_enc", "dip_lin", "clip_hi", "clip_lo", "far_med", "facade_med",
           "sun_elev", "aim_dx", "aim_dy"] + [f"r{i}" for i in range(10)])


def srgb_lin(x):
    x = x / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def quad_bg(ys, xs, vals, ey, ex):
    """Robust 2D-quadratic fit to annulus samples; return prediction at (ey,ex) points."""
    A = np.column_stack([np.ones_like(xs), xs, ys, xs * xs, xs * ys, ys * ys]).astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
    resid = vals - A @ coef
    mad = np.median(np.abs(resid - np.median(resid))) + 1e-9
    keep = np.abs(resid) < 4 * 1.4826 * mad
    if keep.sum() > 12:
        coef, *_ = np.linalg.lstsq(A[keep], vals[keep], rcond=None)
        resid = vals[keep] - A[keep] @ coef
        mad = np.median(np.abs(resid - np.median(resid))) + 1e-9
    E = np.column_stack([np.ones_like(ex), ex, ey, ex * ex, ex * ey, ey * ey]).astype(np.float64)
    return E @ coef, float(mad)


def main():
    frame_p, role, exp_u, gain = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    img = cv2.imread(frame_p)
    if img is None:
        return
    H, W = img.shape[:2]
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    day = ts[:10].replace("-", "")
    try:
        import solar
        elev = round(solar.sun_elevation(datetime.datetime.now(datetime.timezone.utc)), 2)
    except Exception:
        elev = ""
    aim_dx = aim_dy = ""
    try:
        last = open(os.path.join(HERE, "drift.csv")).readlines()[-1].split(",")
        aim_dx, aim_dy = last[1], last[2]
    except Exception:
        pass
    # E_win proxy: sunlit facade patch luminance (right building face), per channel below
    fy, fx = int(0.60 * H), int(0.68 * W)
    facade = img[fy:fy + 100, fx:fx + 100]

    yy, xx = np.mgrid[0:H, 0:W]
    os.makedirs(os.path.join(LAB, "crops", day), exist_ok=True)
    new = not os.path.exists(CSVP)
    os.makedirs(LAB, exist_ok=True)
    rows = []
    hhmmss = ts[11:19].replace(":", "")
    for name, cx, cy in PATCHES:
        rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        valid = (yy >= TOP_MASK)
        disk = (rr < R_DISK) & valid
        ann = (rr >= R_ANN0) & (rr < R_ANN1) & valid
        far = (rr >= R_FAR0) & (rr < R_FAR1) & valid
        ay, ax = np.where(ann)
        dy_, dx_ = np.where(disk)
        for ch in range(3):
            C = img[:, :, ch].astype(np.float64)
            dv, av = C[disk], C[ann]
            bg_pred, ann_mad_fit = quad_bg(ay - cy, ax - cx, C[ann], dy_ - cy, dx_ - cx)
            bg = float(np.median(bg_pred))
            dmed = float(np.median(dv))
            dip_enc = 1.0 - dmed / max(bg, 1e-6)
            dip_lin = 1.0 - float(srgb_lin(np.array(dmed))) / max(float(srgb_lin(np.array(bg))), 1e-9)
            row = dict(ts_utc=ts, role=role, exp_units=exp_u, gain=gain, linz=LINZ,
                       patch=name, cx=cx, cy=cy, ch="bgr"[ch],
                       disk_med=round(dmed, 2),
                       disk_mad=round(float(np.median(np.abs(dv - dmed))), 2),
                       ann_med=round(float(np.median(av)), 2),
                       ann_mad=round(float(np.median(np.abs(av - np.median(av)))), 2),
                       bg_fit=round(bg, 2), ann_resid_mad=round(ann_mad_fit, 3),
                       dip_enc=round(dip_enc, 5), dip_lin=round(dip_lin, 5),
                       clip_hi=round(float((C[disk | ann] > 250).mean()), 4),
                       clip_lo=round(float((C[disk | ann] < 5).mean()), 4),
                       far_med=round(float(np.median(C[far])), 2) if far.any() else "",
                       facade_med=round(float(np.median(facade[:, :, ch])), 2),
                       sun_elev=elev, aim_dx=aim_dx, aim_dy=aim_dy)
            if name == "spot":
                for i in range(10):
                    m = (rr >= i * 15) & (rr < (i + 1) * 15) & valid
                    row[f"r{i}"] = round(float(np.median(C[m])), 2) if m.any() else ""
            rows.append(row)
        # 448 crop (contains disk + annulus; far ring comes from the full frame above)
        y0 = max(0, cy - CROP // 2); x0 = max(0, cx - CROP // 2)
        crop = img[y0:y0 + CROP, x0:x0 + CROP]
        cv2.imwrite(os.path.join(LAB, "crops", day, f"{hhmmss}_{role}_{name}.png"), crop)
    with open(CSVP, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[spotlab] {role} exp={exp_u} gain={gain}: {len(rows)} rows")


if __name__ == "__main__":
    try:
        if len(sys.argv) >= 5 and sys.argv[1] == "log":
            sys.argv.pop(1)
        main()
    except Exception as e:
        print(f"[spotlab] error: {e}", file=sys.stderr)
        sys.exit(0)
