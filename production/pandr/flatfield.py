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
LOCKF = os.path.join(HERE, ".spot_lock.json")    # last per-tick template-lock measurement
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
        T = np.load(TMAP)
        want = mm.get("tmap_sha256_16")
        if want:
            import hashlib
            if hashlib.sha256(T.tobytes()).hexdigest()[:16] != want:
                raise ValueError("tmap/model hash mismatch (torn publish?)")
        if not np.isfinite(T).all():
            raise ValueError("tmap contains non-finite values")
        return T, int(m["x0"]), int(m["y0"]), int(m["half"]), mm.get("scurve"), mm
    except Exception as e:
        print("flatfield tmap rejected:", e, file=sys.stderr)
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
        T, x0, y0, h, sc, mm = tm
        # --- per-tick placement (2026-07-28): the blemish moves continuously (7-18 px
        #     WITHIN a day, measured), so the dip content is aligned to the velocity-
        #     extrapolated position on every tick. The window origin stays at the
        #     published clamped values and the map CONTENT is warped (border T=1), because
        #     shifting the origin cannot follow a blemish this close to the frame edge.
        #     Anchor is tmap.mx/my -- where the dip content actually sits in the stack.
        #     Back-compatible: models without the fields behave exactly as before. -------
        tmm = (mm or {}).get("tmap", {}) or {}
        rx = float(tmm.get("mx", tmm.get("bx", h)))
        ry = float(tmm.get("my", tmm.get("by", h)))
        vel, fe = (mm or {}).get("velocity"), (mm or {}).get("fit_epoch")
        if vel and fe:
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                age = (now - datetime.datetime.fromisoformat(
                    mm.get("updated", fe))).total_seconds() / 86400.0
                if age > 7.0:
                    raise RuntimeError(f"model {age:.1f}d old, skipping tmap path")
                dt = (now - datetime.datetime.fromisoformat(fe)).total_seconds() / 86400.0
                dt = min(max(dt, -0.5), 3.5)
                sp0 = (mm.get("spots") or [{}])[0]
                px = float(sp0.get("fx", 0)) * W + float(vel[0]) * dt
                py = float(sp0.get("fy", 0)) * H + float(vel[1]) * dt
                # prefer the most recent per-tick MEASUREMENT over pure prediction:
                # the prediction only uses data through the fit epoch, the last lock
                # saw the blemish minutes ago. Gated: fresh (<6 h), and within 40 px of
                # the prediction so a corrupted lock can never walk the correction away.
                try:
                    lk = json.load(open(LOCKF))
                    lts = datetime.datetime.fromisoformat(lk["ts"])
                    lage = (now - lts).total_seconds() / 86400.0
                    if 0.0 <= lage < 0.25 and lk.get("corr", 0) >= 0.45:
                        lx2 = float(lk["cx"]) + float(vel[0]) * lage
                        ly2 = float(lk["cy"]) + float(vel[1]) * lage
                        if ((lx2 - px) ** 2 + (ly2 - py) ** 2) ** 0.5 <= 40.0:
                            px, py = lx2, ly2
                except Exception:
                    pass
                sxp = px - (x0 + rx)
                syp = py - (y0 + ry)
                mag = (sxp * sxp + syp * syp) ** 0.5
                if mag > 40.0:                       # cap total extrapolated displacement
                    sxp, syp = sxp * 40.0 / mag, syp * 40.0 / mag
                if abs(sxp) > 0.5 or abs(syp) > 0.5:
                    M2 = np.float32([[1, 0, sxp], [0, 1, syp]])
                    T = np.stack([cv2.warpAffine(T[:, :, c], M2, (2 * h, 2 * h),
                                                 flags=cv2.INTER_LINEAR,
                                                 borderMode=cv2.BORDER_CONSTANT,
                                                 borderValue=1.0) for c in range(3)], axis=2)
                    rx, ry = rx + sxp, ry + syp
            except RuntimeError as e:
                print("flatfield tmap:", e, file=sys.stderr)
                tm = None
        if tm is not None and x0 >= 0 and y0 >= 0 and y0 + 2 * h <= H and x0 + 2 * h <= W and T.shape == (2 * h, 2 * h, 3):
            win = out[y0:y0 + 2 * h, x0:x0 + 2 * h]
            yy, xx = np.mgrid[0:2 * h, 0:2 * h]
            # --- TEMPLATE LOCK: measure where the dip actually is in THIS frame and snap
            #     the map to it. Prediction alone cannot follow intraday migration.
            try:
                gray = _srgb_lin(win.mean(axis=2).astype(np.float32))
                bgs = cv2.GaussianBlur(gray, (0, 0), 45)
                dmap = cv2.GaussianBlur(np.clip(1.0 - gray / np.maximum(bgs, 1e-6), -0.1, 0.3)
                                        .astype(np.float32), (0, 0), 4)
                tpl0 = cv2.GaussianBlur((1.0 - T.mean(axis=2)).astype(np.float32), (0, 0), 4)
                ty0, ty1 = int(max(ry - 60, 0)), int(min(ry + 60, 2 * h))
                tx0, tx1 = int(max(rx - 60, 0)), int(min(rx + 60, 2 * h))
                tpl = tpl0[ty0:ty1, tx0:tx1]
                sy0, sy1 = max(ty0 - 25, 0), min(ty1 + 25, 2 * h)
                sx0, sx1 = max(tx0 - 25, 0), min(tx1 + 25, 2 * h)
                cc = cv2.matchTemplate(dmap[sy0:sy1, sx0:sx1], tpl, cv2.TM_CCOEFF_NORMED)
                _, pk, _, loc = cv2.minMaxLoc(cc)
                # ambiguity gate (review 2026-07-28): a decisive lock needs a clear
                # winner -- mask the best peak and require the runner-up to be lower by
                # a margin, else a cloud edge or contrail can tie and we must not lock.
                cc2 = cc.copy()
                my0, mx0 = max(0, loc[1] - 8), max(0, loc[0] - 8)
                cc2[my0:loc[1] + 9, mx0:loc[0] + 9] = -1.0
                pk2 = float(cc2.max()) if cc2.size else -1.0
                if pk >= 0.45 and (pk - pk2) >= 0.08:
                    lx = (sx0 + loc[0]) - tx0
                    ly = (sy0 + loc[1]) - ty0
                    if abs(lx) <= 25 and abs(ly) <= 25:
                        if abs(lx) > 1 or abs(ly) > 1:
                            M3 = np.float32([[1, 0, lx], [0, 1, ly]])
                            T = np.stack([cv2.warpAffine(T[:, :, c], M3, (2 * h, 2 * h),
                                                         flags=cv2.INTER_LINEAR,
                                                         borderMode=cv2.BORDER_CONSTANT,
                                                         borderValue=1.0) for c in range(3)], axis=2)
                            rx, ry = rx + lx, ry + ly
                        # persist the measurement: it becomes the next tick's prior
                        try:
                            tmp = LOCKF + ".tmp"
                            with open(tmp, "w") as fh:
                                json.dump({"ts": datetime.datetime.now(datetime.timezone.utc)
                                           .isoformat(timespec="seconds"),
                                           "cx": round(x0 + rx, 1), "cy": round(y0 + ry, 1),
                                           "corr": round(float(pk), 3)}, fh)
                            os.replace(tmp, LOCKF)
                        except Exception:
                            pass
            except Exception:
                pass
            rb2 = np.sqrt((xx - rx) ** 2 + (yy - ry) ** 2)
            ring = (rb2 >= 105) & (rb2 < 130)
            if ring.sum() < 400:                     # near the edge: accept a partial ring
                ring = (rb2 >= 80) & (rb2 < 130)
            foot = cv2.GaussianBlur(((1.0 - T.min(axis=2)) > 0.006).astype(np.float32), (0, 0), 8)
            chn = "bgr"
            core = rb2 < 25
            ay2, ax2 = np.where(ring)
            A2 = np.column_stack([np.ones_like(ax2), ax2 - rx, ay2 - ry,
                                  (ax2 - rx) ** 2, (ax2 - rx) * (ay2 - ry),
                                  (ay2 - ry) ** 2]).astype(np.float64)
            E2 = np.column_stack([np.ones(xx.size), (xx - rx).ravel(), (yy - ry).ravel(),
                                  ((xx - rx) ** 2).ravel(), ((xx - rx) * (yy - ry)).ravel(),
                                  ((yy - ry) ** 2).ravel()]).astype(np.float64)
            for c in range(3):
                lin = _srgb_lin(win[:, :, c])
                Dref = 1.0 - T[:, :, c]                          # ref-level per-pixel deficit
                scale = 1.0
                if sc:                                          # EXPOSURE-RESOLVED: scale depth by
                    L = float(np.median(win[:, :, c][ring]))    # this bracket's local level (0-255)
                    s_L = float(np.interp(L, sc["level_knots"], sc["s"][chn[c]]))
                    s_ref = sc.get("s_ref", {}).get(chn[c]) or s_L
                    scale = min(max(s_L / max(s_ref, 1e-3), 0.3), 4.0)
                # --- per-tick AMPLITUDE self-cal (gated) + local-sky quadratic for the cap.
                #     The scurve is open-loop; on dark backgrounds the true dip runs deeper
                #     than map*scurve. When the ring is clean enough to trust, measure this
                #     frame's own core deficit and trim the amplitude, clamped [0.75,1.35].
                vals = lin[ring]
                coef2, *_ = np.linalg.lstsq(A2, vals, rcond=None)
                res2 = vals - A2 @ coef2
                mad2 = float(np.median(np.abs(res2 - np.median(res2)))) * 1.4826 + 1e-9
                keep2 = np.abs(res2) < 3.0 * mad2
                if keep2.sum() > 300:
                    coef2, *_ = np.linalg.lstsq(A2[keep2], vals[keep2], rcond=None)
                B2 = (E2 @ coef2).reshape(lin.shape)
                lvl2 = max(float(np.median(B2[ring])), 1e-6)
                amp_adj = 1.0
                if mad2 / lvl2 < 0.05 and core.sum() > 50:
                    model_core = max(float(np.median(Dref[core])) * scale, 1e-4)
                    meas = 1.0 - float(np.median(lin[core])) / max(float(np.median(B2[core])), 1e-6)
                    if 0.004 <= meas <= 0.22:
                        amp_adj = min(max(meas / model_core, 0.75), 1.35)
                Tc = np.clip(1.0 - Dref * scale * amp_adj, 0.5, 1.05)
                gain = 1.0 / np.clip(Tc, 0.7, 1.0)              # divide out transmittance at this exposure
                corrected = lin * gain
                # FREQUENCY-SPLIT cap (2026-07-28): cap only the SMOOTH component at the
                # local sky fit and pass the grain through. The old pixelwise cap clipped
                # the upper half of the noise onto the noiseless quadratic, leaving an
                # unnaturally quiet disc; an inverse bright spot is a low-frequency
                # object, so clipping blob-scale structure is the whole guarantee.
                lowc = cv2.GaussianBlur(corrected.astype(np.float32), (0, 0), 8).astype(np.float64)
                lowl = cv2.GaussianBlur(lin.astype(np.float32), (0, 0), 8).astype(np.float64)
                corrected = np.minimum(lowc, np.maximum(B2, lowl)) + (corrected - lowc)
                win[:, :, c] = _lin_srgb(lin * (1.0 - foot) + corrected * foot)
            return np.clip(out, 0, 255).astype(np.uint8)
    # (a stale model falls through to the ring-fit fallback below)

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
