#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""flatfield.py — remove fixed window/lens spots by PER-FRAME, PER-CHANNEL footprint flattening.

Design (2026-07-21, replacing the old single-gain map). For each seeded spot we work in a small
window in LINEAR light, per channel:

  1. Fit a smooth quadratic model of the CLEAN SKY from a ring just outside the blemish
     footprint (that ring follows any gradient/vignette, exactly like the annulus method).
  2. Divide that model back in over a FEATHERED footprint, CLAMPED TO BRIGHTENING ONLY — so
     there is no unsharp halo and nothing outside the footprint is touched.

Why per-frame and per-channel: the apparent dip is NOT exposure-independent (the tone curve is
uncalibrated — a dark bracket reads a much deeper dip than a bright one) and it is chromatic.
Letting each frame/channel measure and flatten its OWN deficit sidesteps the tone-curve and the
t/s identifiability problem entirely. This must run on the RAW frames BEFORE Mertens+enhance:
enhance()'s CLAHE+contrast amplifies a fused dip ~3x, so correcting after fusion under-corrects.

Two gates keep it from eating real sky (a cloud that happens to sit on the spot): if the
surrounding ring isn't clean (uniformity gate) or the core deficit is too deep to be the
few-percent blemish (magnitude gate), the spot is left uncorrected for that frame.

Spots are SEEDED by location (SPOTS_SEED, fractional coords) — the sky's lower edge and the
construction crane fool a blind detector, and we know where the real blemishes are. Add entries
as new ones appear (the nightly spot maps are where new ones show up first).

APPLICATION IS FLAG-GATED (see apply()): create ~/monitor/.spot_apply_on to enable. It ships
OFF; re-enable only after the SPOT.md §1 shadow-mode validation (7 days meeting S1-S3).

  flatfield.py build [GLOB]   # -> .spot_gain.npy (legacy) + dim% history log (still useful)
  flatfield.py apply IN OUT   # apply the correction to one image (respects the flag)
"""
import cv2, numpy as np, glob, os, sys, datetime, json
HERE = os.path.dirname(os.path.abspath(__file__))
GAIN = os.path.join(HERE, ".spot_gain.npy")
LOG  = os.path.join(HERE, ".spot_log.csv")
MODEL = os.path.join(HERE, "spot_model.json")   # written by the daily refiner (akdeniz)
TMAP  = os.path.join(HERE, "spot_tmap.npy")      # empirical per-pixel transmittance window (refiner)

# --- known blemishes, fractional (fx, fy) so this is resolution-independent -----------------
SPOTS_SEED = [(0.303, 0.087)]        # window spot at (1163,187)@4K; extend as new ones appear


def _spot_models():
    """Refiner's spot_model.json (drift-tracked location + characterized depth/size) if present and
    sane; else the seed. Each entry: {fx, fy, depth(0-1 or None), r_px(px or None)}. A bad/absent
    model can never make the correction worse than the known-good seed location."""
    seed = [{"fx": f, "fy": g, "depth": None, "r_px": None} for f, g in SPOTS_SEED]
    try:
        out = []
        for s in json.load(open(MODEL)).get("spots", []):
            fx, fy = float(s["fx"]), float(s["fy"])
            if not (0.0 < fx < 1.0 and 0.0 < fy < 1.0):
                continue
            d, r = s.get("amp_pct"), s.get("r_px")
            out.append({"fx": fx, "fy": fy,
                        "depth": (float(d) / 100.0) if d not in (None, "") else None,
                        "r_px": float(r) if r not in (None, "") else None})
        return out or seed
    except Exception:
        return seed


def _load_tmap():
    """Empirical per-pixel, per-channel transmittance window from the refiner (spot_tmap.npy) at
    the full-frame origin recorded in spot_model.json 'tmap'. Returns (T, x0, y0, half) or None.
    The corrector DIVIDES by this — removing the fixed attenuation while PRESERVING whatever is
    behind the spot (cloud texture included), and tracking the current drifted location."""
    try:
        mm = json.load(open(MODEL))
        m = mm.get("tmap")
        if not m:
            return None
        return np.load(TMAP), int(m["x0"]), int(m["y0"]), int(m["half"]), mm.get("scurve")
    except Exception:
        return None

# --- corrector geometry, as fractions of frame WIDTH (validated at 4K: foot95 ring100-150) --
FOOT_FRAC  = 0.0247                  # footprint radius (~95 px @4K) — full-strength core
FEATH_FRAC = 0.0068                  # feather width   (~26 px)      — ramp to 0 at footprint edge
RING0_FRAC = 0.0260                  # clean-sky ring inner (~100 px)
RING1_FRAC = 0.0391                  # clean-sky ring outer (~150 px)
GAIN_MAX   = 2.5                     # hard cap on the multiplicative gain (safety)
# --- gates: skip a spot on a frame where the surroundings aren't clean sky ------------------
RING_UNIF_TOL = 0.06                 # max ring |residual| / level after the quad fit
DIP_MIN, DIP_CAP = 0.004, 0.22       # correct only deficits in this range (else nothing / cloud).
                                     # Prominent spots (bright/clear: 3-7% dip) correct well; on dim
                                     # overcast the dip is at the noise floor (~0.4%) and is left alone
                                     # (a model-based depth floor from the refiner is the robust fix).


def _srgb_lin(v):
    v = v / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _lin_srgb(L):
    L = np.clip(L, 0.0, 1.0)
    return np.where(L <= 0.0031308, 12.92 * L, 1.055 * L ** (1 / 2.4) - 0.055) * 255.0


def _correct(img):
    """Ungated correction: flatten every seeded spot on one BGR frame. Returns a new frame
    (or the input unchanged if no spot passed its gates). Fail-soft is handled by apply()."""
    out = img.astype(np.float32)
    H, W = img.shape[:2]

    # --- PRIMARY: divide by the refiner's empirical transmittance map, if present. This removes
    #     the fixed attenuation while preserving whatever is behind the spot (cloud OR clear sky),
    #     needs no clean ring, and tracks the current drifted location. Gain is capped at 1/0.7 so
    #     map noise can't blow up; the divide only brightens, so no inverse spot. ------------------
    tm = _load_tmap()
    if tm is not None:
        T, x0, y0, h, sc = tm
        if x0 >= 0 and y0 >= 0 and y0 + 2 * h <= H and x0 + 2 * h <= W and T.shape == (2 * h, 2 * h, 3):
            win = out[y0:y0 + 2 * h, x0:x0 + 2 * h]
            yy, xx = np.mgrid[0:2 * h, 0:2 * h]
            ring = (np.sqrt((xx - h) ** 2 + (yy - h) ** 2) >= 105) & (np.sqrt((xx - h) ** 2 + (yy - h) ** 2) < 130)
            foot = cv2.GaussianBlur(((1.0 - T.min(axis=2)) > 0.006).astype(np.float32), (0, 0), 8)
            chn = "bgr"
            for c in range(3):
                lin = _srgb_lin(win[:, :, c])
                Dref = 1.0 - T[:, :, c]                          # ref-level per-pixel deficit
                scale = 1.0
                if sc:                                          # EXPOSURE-RESOLVED: scale depth by
                    L = float(np.median(win[:, :, c][ring]))    # this bracket's local level (0-255)
                    s_L = float(np.interp(L, sc["level_knots"], sc["s"][chn[c]]))
                    s_ref = sc.get("s_ref", {}).get(chn[c]) or s_L
                    scale = min(max(s_L / max(s_ref, 1e-3), 0.3), 4.0)
                Tc = np.clip(1.0 - Dref * scale, 0.5, 1.05)
                gain = 1.0 / np.clip(Tc, 0.7, 1.0)              # divide out transmittance at this exposure
                win[:, :, c] = _lin_srgb(lin * (1.0 - foot) + (lin * gain) * foot)
            return np.clip(out, 0, 255).astype(np.uint8)

    # --- FALLBACK (no transmittance map): ring-fit self-calibration + characterized-depth floor ---
    foot, feath = FOOT_FRAC * W, FEATH_FRAC * W
    r0, r1 = RING0_FRAC * W, RING1_FRAC * W
    half = int(r1 + 0.01 * W)
    sigma = max(foot * 0.05, 1.5)
    changed = False
    for sm in _spot_models():
        fx, fy = sm["fx"], sm["fy"]
        cx, cy = fx * W, fy * H
        x0, x1 = max(0, int(cx - half)), min(W, int(cx + half))
        y0, y1 = max(0, int(cy - half)), min(H, int(cy + half))
        win = out[y0:y1, x0:x1]
        if win.shape[0] < 60 or win.shape[1] < 60:
            continue
        lx, ly = cx - x0, cy - y0
        yy, xx = np.mgrid[0:win.shape[0], 0:win.shape[1]]
        rb = np.sqrt((xx - lx) ** 2 + (yy - ly) ** 2)
        ring = (rb >= r0) & (rb < r1)
        core = rb < foot * 0.3
        if ring.sum() < 200 or core.sum() < 20:
            continue
        w = np.clip((foot - rb) / feath, 0.0, 1.0)           # feathered footprint weight
        ay, ax = np.where(ring)
        A = np.column_stack([np.ones_like(ax), ax - lx, ay - ly,
                             (ax - lx) ** 2, (ax - lx) * (ay - ly), (ay - ly) ** 2]).astype(np.float64)
        E = np.column_stack([np.ones(win.shape[0] * win.shape[1]), (xx - lx).ravel(), (yy - ly).ravel(),
                             ((xx - lx) ** 2).ravel(), ((xx - lx) * (yy - ly)).ravel(),
                             ((yy - ly) ** 2).ravel()]).astype(np.float64)

        # --- gates (on linear luma): is the surround clean sky, and is the deficit spot-sized? ---
        luma = 0.114 * win[:, :, 0] + 0.587 * win[:, :, 1] + 0.299 * win[:, :, 2]
        Ll = _srgb_lin(luma)
        coefL, *_ = np.linalg.lstsq(A, Ll[ring], rcond=None)
        BL = (E @ coefL).reshape(win.shape[:2])
        lvl = max(float(np.median(BL[ring])), 1e-6)
        if float(np.median(np.abs(Ll[ring] - BL[ring]))) / lvl > RING_UNIF_TOL:
            continue                                          # cloud edge / structure in the ring
        OL = cv2.GaussianBlur(Ll, (0, 0), sigma)
        core_deficit = 1.0 - float(np.median(OL[core])) / max(float(np.median(BL[core])), 1e-6)
        if core_deficit > DIP_CAP:
            continue                                          # too deep => a cloud is sitting on the spot

        # Per-frame self-cal engages for a measurable dip; the refiner's characterized DEPTH is a
        # FLOOR that still removes the spot when a single frame's dip is at the noise floor (dim/
        # overcast). Shape S = Gaussian at the drift-tracked centre (from the refiner). Both are
        # capped at the local sky level (add <= B - lin), so neither can make an inverse bright spot.
        selfcal = core_deficit >= DIP_MIN
        depth, r_px = sm.get("depth"), sm.get("r_px")
        use_floor = depth is not None and r_px is not None and depth > 0
        S = np.exp(-0.5 * (rb / max(r_px, 1.0)) ** 2) if use_floor else None
        if not selfcal and not use_floor:
            continue                                          # nothing measurable and no model floor
        for c in range(3):
            lin = _srgb_lin(win[:, :, c])
            coef, *_ = np.linalg.lstsq(A, lin[ring], rcond=None)
            B = (E @ coef).reshape(win.shape[:2])
            add = np.zeros_like(lin)
            if selfcal:                                          # measured self-calibrated flatten
                O = cv2.GaussianBlur(lin, (0, 0), sigma)
                g = np.clip(B / np.maximum(O, 1e-6), 1.0, GAIN_MAX)
                add = w * lin * (g - 1.0)
            if use_floor:                                        # characterized-depth floor
                add = np.maximum(add, w * depth * S * B)
            add = np.minimum(add, np.maximum(B - lin, 0.0))      # cap: never exceed local sky
            win[:, :, c] = _lin_srgb(lin + add)
        changed = True
    return np.clip(out, 0, 255).astype(np.uint8) if changed else img


def apply(img):
    # FLAG-GATED. Ships OFF while the SPOT.md shadow-mode validation runs. Create the flag file
    # .spot_apply_on to enable. Fail-soft: any error returns the frame untouched so a bad frame
    # never breaks a tick. NOTE: the measurement stack (spotlab) must keep seeing RAW frames — do
    # not route its captures through apply() when enabling.
    if img is None or not os.path.exists(os.path.join(HERE, ".spot_apply_on")):
        return img
    try:
        return _correct(img)
    except Exception as e:
        print("flatfield.apply skipped:", e, file=sys.stderr)
        return img


# --- legacy dim% history builder (no longer feeds apply(); kept for the long-term record) ----
SKY_FRAC = 0.30
GW = 960
MAXN = 200
DISK_R_FRAC = 0.020


def build(globpat):
    fs = sorted(glob.glob(globpat))
    if len(fs) > MAXN:
        fs = fs[::max(1, len(fs) // MAXN)][:MAXN]
    grays, full = [], None
    for f in fs:
        im = cv2.imread(f)
        if im is None:
            continue
        if full is None:
            full = im.shape[:2]
        g = cv2.cvtColor(im[:int(SKY_FRAC * im.shape[0])], cv2.COLOR_BGR2GRAY).astype(np.float32)
        g = cv2.resize(g, (GW, max(1, int(GW * g.shape[0] / g.shape[1]))))
        grays.append(g / max(float(g.mean()), 1e-3))
    if len(grays) < 4:
        print(f"need >=4 frames, have {len(grays)}"); return
    H, W = full
    A = np.median(np.stack(grays), axis=0)
    gh, gw = A.shape; s = W / gw
    yy, xx = np.mgrid[0:gh, 0:gw]
    gain_sky = np.ones((gh, gw), np.float32)
    Rd = DISK_R_FRAC * gw; Rout = Rd * 2.2
    spots = []
    for fx, fy in SPOTS_SEED:
        cx, cy = fx * gw, fy * H / s
        rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        ann = (rr >= Rd) & (rr < Rout)
        if ann.sum() < 25:
            continue
        bg = float(np.median(A[ann]))
        disk = rr < Rd
        gain_sky[disk] = np.clip(bg / np.maximum(A, 1e-3), 1.0, 1.3)[disk]
        att = float(np.median(A[rr < Rd * 0.45])) / bg
        spots.append((int(cx * s), int(cy * s), att))
    gain_sky = cv2.GaussianBlur(gain_sky, (0, 0), 2)
    gwH = int(GW * H / W)
    gain = np.ones((gwH, gw), np.float32); gain[:gh] = gain_sky
    np.save(GAIN, gain)
    print(f"built from {len(grays)} frames; {len(spots)} seeded spot(s):")
    for x, y, att in spots:
        print(f"  ({x},{y})  dim={100*(1-att):.2f}%")
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    new = not os.path.exists(LOG)
    with open(LOG, "a") as fh:
        if new:
            fh.write("ts_utc,n_frames,x,y,dim_pct\n")
        for x, y, att in spots:
            fh.write(f"{ts},{len(grays)},{x},{y},{100*(1-att):.2f}\n")
    print(f"saved {GAIN} ; logged to {os.path.basename(LOG)}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build(sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/monitor/flatfield_acc/*.png"))
    elif len(sys.argv) > 3 and sys.argv[1] == "apply":
        cv2.imwrite(sys.argv[3], apply(cv2.imread(sys.argv[2])))
    else:
        print(__doc__)
