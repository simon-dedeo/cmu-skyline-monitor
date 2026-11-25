#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""flatfield.py -- remove the window blemish from raw HDR brackets before fusion.

Uses the nightly bundle published from akdeniz (spot_model.json schema 3 + spot_tmap.npy):
  * Empirical per-channel Camera Response Function (CRF) for true linear radiance.
  * Bayesian Stan M2 physical prior: d_prior = core_scale * ((1 - t) - k_fac * L_fac / L_bg),
    accounting for direct-sun forward scattering off the dust grain in Red/Green.
  * Hierarchical Bayesian MAP matched-filter depth estimation across all unclipped brackets
    in the set (smoothly shrinking to the Stan M2 prior under clouds; clipped-ring brackets
    inherit the unclipped posterior depth so partially-clipped bright sky is also corrected).
  * Ridge-regularized sky background with TOP_MASK=20 (stable even at cy~68 near top edge).

Flag-gated: create ~/monitor/.spot_apply_on to enable. SPOT_NO_LOCK=1 disables template lock;
SPOT_NOW=<ISO> replays at a past timestamp. Fail-soft: any error returns frames untouched.

  flatfield.py apply OUT IN...
"""
import cv2, numpy as np, os, sys, datetime, json, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "spot_model.json")
TMAP = os.path.join(HERE, "spot_tmap.npy")
LOCKF = os.path.join(HERE, ".spot_lock.json")
PROV = os.path.join(HERE, "spot_provenance.csv")
FLAG = os.path.join(HERE, ".spot_apply_on")
CODE_VERSION = "2026-11-24-bayes3"

MAX_FIT_AGE_DAYS = 30.0
EXTRAP_DAYS = (-0.5, 3.5)
SHIFT_CAP = 40.0
TOP_MASK = 20
RING0, RING1 = 85, 115
R_CORE = 25
RIDGE_QUAD = (50.0, 50.0, 200.0)
LOCK_MIN_PK, LOCK_MARGIN, LOCK_RANGE, LOCK_WINDOW_H = 0.50, 0.08, 25, 1.0
DEPTH_CLAMP = (-0.015, 0.14)
T_CLAMP = (0.84, 1.02)
DEFAULT_CRF = {"b": [0.55, 3.8], "g": [0.45, 3.8], "r": [0.36, 3.2]}


def _crf_eval(v, a, g):
    x = np.clip(np.asarray(v, np.float64) / 255.0, 0.0, 1.0)
    c0 = (a / (1.0 + a)) ** g
    return (((x + a) / (1.0 + a)) ** g - c0) / (1.0 - c0)


def _crf_inv(L, a, g):
    L = np.clip(np.asarray(L, np.float64), 0.0, 1.0)
    c0 = (a / (1.0 + a)) ** g
    x = ((L * (1.0 - c0) + c0) ** (1.0 / g)) * (1.0 + a) - a
    return np.clip(x, 0.0, 1.0) * 255.0


def _now():
    v = os.environ.get("SPOT_NOW")
    return datetime.datetime.fromisoformat(v) if v else datetime.datetime.now(datetime.timezone.utc)


# ----------------------------------------------------------------------------- model
def load_model(now=None):
    """(T, model) or raise. Rejects bad schema, torn hash, non-finite T, or expired fit."""
    now = now or _now()
    mm = json.load(open(MODEL))
    if mm.get("schema") not in (2, 3) or not mm.get("tmap"):
        raise ValueError(f"unsupported model schema {mm.get('schema')!r}")
    T = np.load(TMAP)
    want = mm.get("tmap_sha256_16")
    if want and hashlib.sha256(T.tobytes()).hexdigest()[:16] != want:
        raise ValueError("tmap/model hash mismatch")
    if not np.isfinite(T).all():
        raise ValueError("tmap contains non-finite values")
    h = int(mm["tmap"]["half"])
    if T.shape != (2 * h, 2 * h, 3):
        raise ValueError(f"tmap shape {T.shape} != {(2 * h, 2 * h, 3)}")
    age = (now - datetime.datetime.fromisoformat(mm["fit_epoch"])).total_seconds() / 86400.0
    if age > MAX_FIT_AGE_DAYS:
        raise ValueError(f"fit {age:.1f} d old")
    return T.astype(np.float32), mm


def predicted_position(mm, W, H, now):
    sp = mm["spots"][0]
    px, py = float(sp["fx"]) * W, float(sp["fy"]) * H
    vel = mm.get("velocity") or [0.0, 0.0]
    dt = (now - datetime.datetime.fromisoformat(mm["fit_epoch"])).total_seconds() / 86400.0
    dt = min(max(dt, EXTRAP_DAYS[0]), EXTRAP_DAYS[1])
    return px + float(vel[0]) * dt, py + float(vel[1]) * dt


# ------------------------------------------------------------------------------ lock
def _rival_peak(cc, loc, w=12):
    k = 2 * int(w) + 1
    dil = cv2.dilate(cc, np.ones((k, k), np.uint8))
    ys, xs = np.where(cc >= dil - 1e-9)
    best = -1.0
    for y, x in zip(ys, xs):
        if max(abs(int(y) - loc[1]), abs(int(x) - loc[0])) > w:
            best = max(best, float(cc[y, x]))
    return best


def measure_lock(win, T, rx, ry):
    """Symmetric-range template lock relative to (rx, ry). Skips dark frames."""
    g_raw = win.mean(axis=2).astype(np.float32)
    if float(np.median(g_raw)) < 30.0:
        return None, None, None, None
    h2 = T.shape[0]
    # Keep upward & downward search range symmetric so top-edge clamping never biases dy
    yr = min(LOCK_RANGE, max(0, int(ry) - 48))
    if yr < 6:
        return None, None, None, None
    xr = LOCK_RANGE
    gray = _crf_eval(g_raw, 0.45, 3.8).astype(np.float32)
    bgs = cv2.GaussianBlur(gray, (0, 0), 45)
    dmap = cv2.GaussianBlur(np.clip(1.0 - gray / np.maximum(bgs, 1e-6), -0.1, 0.3).astype(np.float32), (0, 0), 4)
    tpl0 = cv2.GaussianBlur((1.0 - T.mean(axis=2)).astype(np.float32), (0, 0), 4)
    ty0, ty1 = int(max(ry - 45, 0)), int(min(ry + 45, h2))
    tx0, tx1 = int(max(rx - 55, 0)), int(min(rx + 55, h2))
    sy0, sy1 = max(ty0 - yr, 0), min(ty1 + yr, h2)
    sx0, sx1 = max(tx0 - xr, 0), min(tx1 + xr, h2)
    cc = cv2.matchTemplate(dmap[sy0:sy1, sx0:sx1], tpl0[ty0:ty1, tx0:tx1], cv2.TM_CCOEFF_NORMED)
    _, pk, _, loc = cv2.minMaxLoc(cc)
    pk2 = _rival_peak(cc, loc)
    dx, dy = (sx0 + loc[0]) - tx0, (sy0 + loc[1]) - ty0
    if pk >= LOCK_MIN_PK and (pk - pk2) >= LOCK_MARGIN and abs(dx) <= xr and abs(dy) <= yr:
        return float(dx), float(dy), float(pk), pk2
    return None, None, float(pk), pk2


def _lock_history(now):
    try:
        hist = json.load(open(LOCKF))
        if not isinstance(hist, list):
            return []
    except Exception:
        return []
    cut = now - datetime.timedelta(hours=LOCK_WINDOW_H)
    out = []
    for e in hist:
        try:
            if datetime.datetime.fromisoformat(e["ts"]) >= cut:
                out.append(e)
        except Exception:
            pass
    return out


def _save_lock_history(hist):
    tmp = LOCKF + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(hist[-64:], fh)
    os.replace(tmp, LOCKF)


def _best_bracket(wins):
    best, best_lvl = 0, -1.0
    for i, w in enumerate(wins):
        g = w.mean(axis=2)
        if (g > 252).mean() > 0.02:
            continue
        lvl = float(np.median(g))
        if lvl > best_lvl:
            best, best_lvl = i, lvl
    return best


# --------------------------------------------------------------------------- flatten
def _fit_sky(vals, A, E, R, shape):
    coef = np.linalg.solve(A.T @ A + R, A.T @ vals)
    res = vals - A @ coef
    mad = float(np.median(np.abs(res - np.median(res)))) * 1.4826 + 1e-9
    keep = np.abs(res) < 3.0 * mad
    if keep.sum() > 250:
        Ak = A[keep]
        coef = np.linalg.solve(Ak.T @ Ak + R, Ak.T @ vals[keep])
        res = vals[keep] - Ak @ coef
        mad = float(np.median(np.abs(res - np.median(res)))) * 1.4826 + 1e-9
    return (E @ coef).reshape(shape), mad


def _flatten_set(outs, wins, T, rx, ry, mm):
    """Bayesian MAP matched-filter blemish removal across all brackets in the set."""
    h2 = T.shape[0]
    yy, xx = np.mgrid[0:h2, 0:h2]
    rb = np.hypot(xx - rx, yy - ry)
    ring = (rb >= RING0) & (rb < RING1) & (yy >= TOP_MASK)
    if ring.sum() < 400:
        ring = (rb >= 70) & (rb < 130) & (yy >= TOP_MASK)
    core = (rb < R_CORE) & (yy >= TOP_MASK)
    foot = cv2.GaussianBlur((rb < 82).astype(np.float32), (0, 0), 8)
    valid_m = (foot > 0.05) & (yy >= TOP_MASK)

    ay, ax = np.where(ring)
    ur, vr = (ax - rx) / 100.0, (ay - ry) / 100.0
    ue, ve = ((xx - rx) / 100.0).ravel(), ((yy - ry) / 100.0).ravel()
    A = np.column_stack([np.ones_like(ur), ur, vr, ur * ur, ur * vr, vr * vr])
    E = np.column_stack([np.ones_like(ue), ue, ve, ue * ue, ue * ve, ve * ve])
    pen = RIDGE_QUAD if ry < RING1 + TOP_MASK else (2.0, 2.0, 2.0)
    R = np.diag([0.0, 0.0, 0.0, pen[0], pen[1], pen[2]])

    crf = mm.get("crf") or DEFAULT_CRF
    bayes_ch = (mm.get("bayes") or {}).get("channels") or {}
    core_scales = (mm.get("tmap") or {}).get("core_scale") or {}
    H, W = outs[0].shape[:2]
    fy, fx = int(0.60 * H), int(0.68 * W)

    # Normalized 2D blemish shape P(x,y,c) from TMAP (median in core == 1.0)
    P = np.zeros_like(T, dtype=np.float64)
    d_tmap = np.zeros(3)
    for c in range(3):
        D = np.clip(1.0 - T[:, :, c].astype(np.float64), 0.0, 0.25) * foot
        d0 = max(float(np.median(D[core])), 1e-4)
        d_tmap[c] = d0
        P[:, :, c] = D / d0

    n = len(wins)
    for c, ch in enumerate("bgr"):
        a, g_exp = crf.get(ch, DEFAULT_CRF[ch])
        p = P[:, :, c]
        bc = bayes_ch.get(ch)
        cscale = float(core_scales.get(ch, 1.25))
        tau = max(cscale * float(bc["sig_x"]) * 3.0, 0.022) if bc else 0.025
        tau2 = tau * tau

        # Step 1: gather per-bracket Stan M2 prior and unclipped matched-filter observations
        priors, obs = [], []
        for i, (full, w) in enumerate(zip(outs, wins)):
            raw = w[:, :, c].astype(np.float64)
            Lmed = float(np.median(raw[ring]))
            # Physical prior from Stan M2 (includes facade scattering coupling k_fac)
            d_pr = d_tmap[c]
            if bc and Lmed >= 12:
                Lbg_e = max(float(_crf_eval(Lmed, a, g_exp)), 1e-4)
                Lfac_e = float(_crf_eval(float(np.median(full[fy:fy + 100, fx:fx + 100, c])), a, g_exp))
                fr = min(Lfac_e / Lbg_e, 5.0)
                d_pr = float(np.clip(cscale * ((1.0 - float(bc["t"])) - float(bc["k_fac"]) * fr), -0.01, 0.12))
            priors.append(d_pr)
            # Only unclipped brackets with clean-enough sky contribute observations
            if (raw[ring | core] > 250).mean() > 0.01 or Lmed < 18:
                obs.append(None)
                continue
            lin = _crf_eval(raw, a, g_exp)
            B, mad = _fit_sky(lin[ring], A, E, R, (h2, h2))
            lvl = max(float(np.median(B[ring])), 1e-6)
            rough = mad / lvl
            um = valid_m & (raw < 251) & (raw > 5) & (B > 1e-5)
            if rough > 0.08 or um.sum() < 200:
                obs.append(None)
                continue
            obs_def = np.clip(1.0 - lin[um] / B[um], -0.15, 0.25)
            pw = p[um]
            d_hat = float(np.dot(pw, obs_def) / max(np.dot(pw, pw), 1e-6))
            sig2 = (0.40 * rough) ** 2 + 0.003 ** 2
            obs.append((d_hat, sig2))

        # Step 2: pooled Bayesian MAP posterior across all unclipped brackets in this channel
        d_pr_med = float(np.median(priors))
        prec_sum = 1.0 / tau2
        w_sum = d_pr_med / tau2
        for o in obs:
            if o is not None:
                prec_sum += 1.0 / o[1]
                w_sum += o[0] / o[1]
        d_pool = float(np.clip(w_sum / prec_sum, DEPTH_CLAMP[0], DEPTH_CLAMP[1]))

        # Step 3: apply to EVERY bracket (including partially-clipped bright brackets like br1!)
        # For low-level Red where ISP chroma-matrix knee can vary by bracket, shrink gently toward
        # this bracket's own observation; otherwise use the high-SNR pooled posterior.
        for i, w in enumerate(wins):
            raw = w[:, :, c].astype(np.float64)
            if float(np.median(raw[core])) >= 254.0 or float(np.median(raw[ring])) < 5.0:
                continue
            d_use = d_pool
            if ch == "r" and obs[i] is not None and float(np.median(raw[ring])) < 120.0:
                d_hat, sig2 = obs[i]
                d_use = float(np.clip((d_hat / sig2 + d_pool / (0.018 ** 2)) / (1.0 / sig2 + 1.0 / (0.018 ** 2)),
                                      DEPTH_CLAMP[0], DEPTH_CLAMP[1]))
            lin = _crf_eval(raw, a, g_exp)
            Tc = np.clip(1.0 - d_use * p, T_CLAMP[0], T_CLAMP[1])
            w[:, :, c] = _crf_inv(lin / Tc, a, g_exp)


def _shift(T, sx, sy):
    h2 = T.shape[0]
    M = np.float32([[1, 0, sx], [0, 1, sy]])
    return np.stack([cv2.warpAffine(T[:, :, c], M, (h2, h2), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=1.0)
                     for c in range(3)], axis=2)


def _provenance(row):
    try:
        hdr = not os.path.exists(PROV)
        with open(PROV, "a") as fh:
            if hdr:
                fh.write("ts,model_updated,tmap_sha16,code_version,apply_flag,cx,cy,path,lock,pk,pk2\n")
            fh.write(",".join(str(v) for v in row) + "\n")
    except Exception:
        pass


# ------------------------------------------------------------------------------ main
def correct_set(imgs):
    """Ungated: correct one bracket set (list of BGR uint8 frames, same shape)."""
    now = _now()
    try:
        T, mm = load_model(now)
    except Exception as e:
        print("flatfield: model not applied:", e, file=sys.stderr)
        return imgs
    H, W = imgs[0].shape[:2]
    tm = mm["tmap"]
    x0, y0, h = int(tm["x0"]), int(tm["y0"]), int(tm["half"])
    if not (x0 >= 0 and y0 >= 0 and y0 + 2 * h <= H and x0 + 2 * h <= W):
        return imgs
    rx, ry = float(tm.get("mx", tm.get("bx", h))), float(tm.get("my", tm.get("by", h)))
    out = [im.astype(np.float32) for im in imgs]
    wins = [o[y0:y0 + 2 * h, x0:x0 + 2 * h] for o in out]
    is_hdr = len(imgs) >= 2 or bool(os.environ.get("SPOT_LOG_SINGLE"))

    px, py = predicted_position(mm, W, H, now)
    lock, pk, pk2 = 0, None, None
    if not os.environ.get("SPOT_NO_LOCK"):
        hist = _lock_history(now)
        if is_hdr:
            try:
                dx, dy, pk, pk2 = measure_lock(wins[_best_bracket(wins)], T, rx, ry)
                if dx is not None:
                    lock = 1
                    hist.append({"ts": now.isoformat(timespec="seconds"),
                                 "cx": round(x0 + rx + dx, 1), "cy": round(y0 + ry + dy, 1),
                                 "corr": round(pk, 3)})
                    _save_lock_history(hist)
            except Exception:
                pass
        near = [e for e in hist if np.hypot(e["cx"] - px, e["cy"] - py) <= SHIFT_CAP]
        if len(near) >= 2:
            px = float(np.median([e["cx"] for e in near]))
            py = float(np.median([e["cy"] for e in near]))
    sx, sy = px - (x0 + rx), py - (y0 + ry)
    mag = float(np.hypot(sx, sy))
    if mag > SHIFT_CAP:
        sx, sy = sx * SHIFT_CAP / mag, sy * SHIFT_CAP / mag
    if abs(sx) > 0.5 or abs(sy) > 0.5:
        T = _shift(T, sx, sy)
        rx, ry = rx + sx, ry + sy

    if is_hdr:
        _provenance([now.isoformat(timespec="seconds"), mm.get("updated", ""),
                     mm.get("tmap_sha256_16", ""), CODE_VERSION, int(os.path.exists(FLAG)),
                     round(x0 + rx, 1), round(y0 + ry, 1), f"bayes_s{mm.get('schema')}", lock,
                     None if pk is None else round(pk, 3), None if pk2 is None else round(pk2, 3)])

    _flatten_set(out, wins, T, rx, ry, mm)
    return [np.rint(np.clip(o, 0, 255)).astype(np.uint8) for o in out]


def apply(imgs):
    """Flag-gated, fail-soft entry point. Accepts a list of frames or a single frame."""
    single = isinstance(imgs, np.ndarray)
    lst = [imgs] if single else list(imgs)
    if not lst or any(i is None for i in lst) or not os.path.exists(FLAG):
        return imgs
    try:
        res = correct_set(lst)
    except Exception as e:
        print("flatfield.apply skipped:", e, file=sys.stderr)
        return imgs
    return res[0] if single else res


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "apply":
        ims = [cv2.imread(p) for p in sys.argv[3:]]
        res = apply(ims)
        for i, im in enumerate(res):
            out = sys.argv[2] if len(res) == 1 else sys.argv[2].replace(".", f".br{i}.", 1)
            cv2.imwrite(out, im)
    else:
        print(__doc__)
