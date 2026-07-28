#!/usr/bin/env python3
"""publish_live.py (akdeniz) -- publish the VALIDATED spotnight tracker into the live
corrector contract on pandr (spot_model.json + spot_tmap.npy). Replaces the refiner push.

Why this exists (2026-07-28): the refiner's tracking was triple-anchored to a hardcoded
seed -- search window radius 144 px around (1163,187), accept gate +/-120 px from the
seed, and tmap geometry needing cy>=150 -- so once the blemish walked past those bounds
(it did) the live model froze/degraded while the blemish kept moving at 6-18 px/day,
INCLUDING intraday. Verified by an adversarial panel; three fixes are structural here:

  * REGISTRATION (shift-and-stack): each frame's window is extracted at that frame's
    velocity-interpolated centre, so the dip stacks aligned instead of smeared ~12 px.
  * BLEMISH-LOCAL geometry: window clamped to the frame (top edge!), ring/masks centred
    on the blemish's local position (bx,by), never the window centre.
  * SIGMA-CLIPPED ring background per frame: the un-clipped fit ingested the dip and
    baked a ~half-depth map (simulated 4% -> 2.1%).

Refuses to publish rather than publish junk: n>=15 frames/channel, 0.80 < min_T < 0.995.
"""
import cv2, numpy as np, glob, os, json, datetime, subprocess, sys, hashlib

HOME = os.path.expanduser("~")
FR = HOME + "/refiner/frames"
STATE = HOME + "/spot_state/state.json"
REF_MODEL = HOME + "/refiner/spot_model.json"     # scurve + drift passthrough only
OUTD = HOME + "/spot_state"
PANDR = "proofsandreasons@pandr.wifi.local.cmu.edu"
MAP_DAYS, HALF = 2, 150
FULL_W, FULL_H = 3840, 2160


def srgb_lin(v):
    v = v / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def frame_epoch(path):
    b = os.path.basename(path)          # YYYYMMDD_HHMMSS.png
    return datetime.datetime.strptime(b[:15], "%Y%m%d_%H%M%S").replace(
        tzinfo=datetime.timezone.utc)


def build_tmap_registered(cx, cy, vx, vy, fit_epoch):
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=MAP_DAYS)).strftime("%Y%m%d")
    fs = sorted(f for f in glob.glob(FR + "/*.png") if os.path.basename(f)[:8] >= cutoff)
    if len(fs) > 400:
        fs = fs[::max(1, len(fs) // 400)][:400]
    h = HALF
    # fixed local blemish position, chosen from the FIT-DAY centre with clamping
    x0f = int(round(min(max(cx - h, 0), FULL_W - 2 * h)))
    y0f = int(round(min(max(cy - h, 0), FULL_H - 2 * h)))
    bx, by = cx - x0f, cy - y0f
    yy, xx = np.mgrid[0:2 * h, 0:2 * h]
    rb = np.sqrt((xx - bx) ** 2 + (yy - by) ** 2)
    ring = (rb >= 105) & (rb < 130)
    if ring.sum() < 400:
        return None
    ay, ax = np.where(ring)
    A = np.column_stack([np.ones_like(ax), ax, ay, ax * ax, ax * ay, ay * ay]).astype(float)
    E = np.column_stack([np.ones((2 * h) ** 2), xx.ravel(), yy.ravel(), (xx * xx).ravel(),
                         (xx * yy).ravel(), (yy * yy).ravel()]).astype(float)
    acc = {0: [], 1: [], 2: []}
    for f in fs:
        # REGISTRATION: window origin follows this frame's interpolated centre so the
        # blemish sits at the SAME local (bx,by) in every extracted window.
        try:
            dtd = (frame_epoch(f) - fit_epoch).total_seconds() / 86400.0
        except Exception:
            continue
        cfx, cfy = cx + vx * dtd, cy + vy * dtd
        x0 = int(round(min(max(cfx - bx, 0), FULL_W - 2 * h)))
        y0 = int(round(min(max(cfy - by, 0), FULL_H - 2 * h)))
        im = cv2.imread(f)
        if im is None or y0 + 2 * h > im.shape[0] or x0 + 2 * h > im.shape[1]:
            continue
        for c in range(3):
            g = srgb_lin(im[y0:y0 + 2 * h, x0:x0 + 2 * h, c].astype(float))
            m = np.median(g[ring])
            if m < 0.03 or m > 0.95:
                continue
            vals = g[ring]
            coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
            res = vals - A @ coef                    # sigma-clip: don't let cloud (or any
            mad = np.median(np.abs(res - np.median(res))) * 1.4826 + 1e-9
            keep = np.abs(res) < 3.0 * mad           # residual dip leakage) bias the bg
            if keep.sum() > 300:
                coef, *_ = np.linalg.lstsq(A[keep], vals[keep], rcond=None)
            acc[c].append(np.clip(g / np.maximum((E @ coef).reshape(2 * h, 2 * h), 1e-4), 0, 2))
    n = min(len(acc[c]) for c in range(3))
    if n < 15:
        print(f"publish_live: only {n} usable frames/channel, refusing"); return None
    T = np.stack([np.clip(np.median(np.stack(acc[c]), axis=0), 0.5, 1.05)
                  for c in range(3)], axis=2).astype(np.float32)
    if not (0.80 < float(T.min()) < 0.995):
        print(f"publish_live: min_T={T.min():.3f} outside (0.80,0.995), refusing"); return None
    np.save(OUTD + "/spot_tmap_live.npy", T)
    # content centroid (should sit at ~(bx,by) after registration; recorded for the
    # corrector's warp anchor and as a registration health check)
    D = cv2.GaussianBlur((1.0 - T.mean(axis=2)).astype(np.float32), (0, 0), 5)
    near = rb <= 70
    pk = float(np.percentile(D[near], 99.5))
    mx, my = bx, by
    m = near & (D >= 0.5 * pk) & (D > 0)
    if pk > 0.003 and m.sum() >= 30:
        w = D[m].astype(np.float64)
        mx, my = float((xx[m] * w).sum() / w.sum()), float((yy[m] * w).sum() / w.sum())
    return {"x0": x0f, "y0": y0f, "half": h, "bx": round(bx, 1), "by": round(by, 1),
            "mx": round(mx, 1), "my": round(my, 1), "n": int(n),
            "min_T": round(float(T.min()), 3)}


def main():
    st = json.load(open(STATE))
    cx, cy = st.get("cx"), st.get("cy")
    if cx is None:
        print("publish_live: no position in state"); return 0
    prev = st.get("prev")
    vx = vy = 0.0
    if prev and prev[0] is not None:
        vx, vy = float(cx - prev[0]), float(cy - prev[1])
        vmag = float(np.hypot(vx, vy))
        if vmag > 45:
            vx, vy = vx * 45 / vmag, vy * 45 / vmag
    fit_day = st.get("day") or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    # Prefer the epoch spotnight recorded from the exact frames it fitted (state
    # fit_epoch, added after the 2026-07-28 review); recompute from filenames otherwise.
    state_epoch = st.get("fit_epoch")
    # THE EPOCH IS THE DATA'S, NOT THE RUN'S (2026-07-28 dipole bug): spotnight's "day D"
    # position is fitted on frames from days D-1 and D-2, so its effective observation
    # epoch is ~1.5 days before the run day. Stamping it with the run day made the
    # velocity extrapolation underpredict by v*1.5d ~ 9 px -- a visible dipole (bright
    # arc trailing, dark arc leading). Compute the mean DAYTIME frame timestamp of the
    # two fit days from the ref-frame filenames themselves.
    d0 = datetime.datetime(int(fit_day[:4]), int(fit_day[4:6]), int(fit_day[6:]),
                           tzinfo=datetime.timezone.utc)
    fit_days = {(d0 - datetime.timedelta(days=k)).strftime("%Y%m%d") for k in (1, 2)}
    stamps = []
    for f in glob.glob(FR + "/*.png"):
        b = os.path.basename(f)
        if b[:8] in fit_days:
            try:
                t = frame_epoch(f)
                if 10 <= t.hour <= 23:          # bright-half proxy: daytime frames
                    stamps.append(t)
            except Exception:
                pass
    if state_epoch:
        fit_epoch = datetime.datetime.fromisoformat(state_epoch)
    elif stamps:
        mean_s = sum(t.timestamp() for t in stamps) / len(stamps)
        fit_epoch = datetime.datetime.fromtimestamp(mean_s, tz=datetime.timezone.utc)
    else:
        fit_epoch = d0 - datetime.timedelta(days=1.5)
    print(f"fit_epoch = {fit_epoch.isoformat(timespec='seconds')} "
          f"(mean of {len(stamps)} daytime frames over {sorted(fit_days)})")
    tmap = build_tmap_registered(float(cx), float(cy), vx, vy, fit_epoch)
    if tmap is None:
        print("publish_live: NOT publishing (live keeps last good)"); return 0
    try:
        ref = json.load(open(REF_MODEL))
    except Exception:
        ref = {}
    good = st.get("good") or {}
    tmap_sha = hashlib.sha256(np.load(OUTD + "/spot_tmap_live.npy").tobytes()).hexdigest()[:16]
    model = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
             "schema": 2, "code_version": "2026-07-28-r1", "tmap_sha256_16": tmap_sha,
             "source": "spotnight", "fit_day": fit_day,
             "fit_epoch": fit_epoch.isoformat(timespec="seconds"),
             "spots": [{"fx": round(cx / FULL_W, 5), "fy": round(cy / FULL_H, 5),
                        "amp_pct": round(100 * (1 - tmap["min_T"]), 2), "r_px": 38.0}],
             "velocity": [round(vx, 2), round(vy, 2)],
             "tmap": tmap, "drift": ref.get("drift", {}),
             "acceptance": {k: good.get(k) for k in ("removal", "gain")}}
    if ref.get("scurve"):
        model["scurve"] = ref["scurve"]
    json.dump(model, open(OUTD + "/spot_model_live.json", "w"), indent=2)
    for local, remote in ((OUTD + "/spot_tmap_live.npy", "monitor/spot_tmap.npy"),
                          (OUTD + "/spot_model_live.json", "monitor/spot_model.json")):
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", local, f"{PANDR}:{remote}.new"],
                       check=True, timeout=120)
    subprocess.run(["ssh", "-o", "BatchMode=yes", PANDR,
                    "mv monitor/spot_tmap.npy.new monitor/spot_tmap.npy && "
                    "mv monitor/spot_model.json.new monitor/spot_model.json"],
                   check=True, timeout=60)
    print(f"published: centre=({cx},{cy}) v=({vx:+.1f},{vy:+.1f})/d "
          f"local=({tmap['bx']},{tmap['by']}) content=({tmap['mx']},{tmap['my']}) "
          f"n={tmap['n']} min_T={tmap['min_T']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
