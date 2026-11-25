#!/usr/bin/env python3
"""spotnight.py -- nightly window-blemish tracker, Bayesian M2 fitter, and publisher (akdeniz).

One unified script replaces the old spotnight.py + publish_live.py + pandr/spotfit.R chain:
  1. TRACK position (cx, cy) and velocity (vx, vy) from multi-bracket frames (parallelized).
  2. FIT empirical Camera Response Function (CRF) per channel from same-tick br0/br1/br2
     exposure ratios in photometry.csv (eliminates the sRGB linearization error and scurve).
  3. FIT Bayesian M2 physics model in Stan (cmdstanpy, 4 parallel chains):
         L_spot / L_bg ~ Student-t(nu_c, t_c + k_fac_c * (L_fac / L_bg), hypot(sig, sig_x_c))
     which directly supplies the physical prior (including direct-sun Red scattering!) to
     flatfield.py on pandr.
  4. BUILD a high-SNR registered 2D transmittance map (spot_tmap.npy) from clean, unclipped
     multi-bracket frames (br0/br1/br2) in empirical-CRF linear light with TOP_MASK=20 and
     ridge-regularized sky background (no winter Blue-clipping failure, no edge blow-up).
  5. PUBLISH atomically to pandr (spot_model.json schema 3 + spot_tmap.npy).

Usage:
    spotnight.py                     # full nightly run: track + CRF + Stan + TMAP + push
    spotnight.py --dry-run           # full build locally, push nothing to pandr
    spotnight.py --day 20261124      # track + build for a specific day (no push unless --push)
    spotnight.py --replay A:B        # replay position tracker over YYYYMMDD:YYYYMMDD
"""
import argparse, collections, csv, datetime, glob, hashlib, json, os, subprocess, sys, traceback
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import cv2
from scipy.optimize import minimize

cv2.setNumThreads(1)

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
BRACKETS = os.environ.get("SPOT_BRACKETS", os.path.join(HOME, "brackets"))
PHOT = os.environ.get("SPOT_PHOTOMETRY", os.path.join(HOME, "refiner/photometry.csv"))
STATE_DIR = os.environ.get("SPOT_STATE_DIR", os.path.join(HOME, "spot_state"))
STATE = os.path.join(STATE_DIR, "state.json")
HISTORY = os.path.join(STATE_DIR, "history.csv")
BAYES_OUT = os.path.join(STATE_DIR, "bayes_fit.json")
OUT_MODEL = os.path.join(STATE_DIR, "spot_model_live.json")
OUT_TMAP = os.path.join(STATE_DIR, "spot_tmap_live.npy")
LOG = os.path.join(STATE_DIR, "spotnight.log")
MARKER = os.path.join(STATE_DIR, "last_run_ok")
STAN_FILE = os.path.join(HERE, "spot_m2.stan")
PANDR = os.environ.get("SPOT_PANDR", "proofsandreasons@pandr.wifi.local.cmu.edu")
SEED = json.loads(os.environ.get("SPOT_SEED", "[1184,176]"))
CODE_VERSION = "2026-11-24-bayes3"
WORKERS = int(os.environ.get("SPOT_WORKERS", "8"))

FULL_W, FULL_H = 3840, 2160
HALF = 150                   # half-window for stacks and published TMAP (300x300)
TOP_MASK = 20                # exclude top rows (lens shading / ISP edge)
RING0, RING1 = 85, 115       # clean sky annulus (closer + better symmetry near top edge)
R_CORE = 25
R_DISK = 60                  # matches spotlab.py disk radius for Stan core_scale conversion
R_PX = 38.0
RIDGE_QUAD = (50.0, 50.0, 200.0)   # quadratic ridge penalty (damps one-sided top-edge curvature)

# ---- tracking parameters
TRACK_DAYS = (2, 4)          # try 2-day window, widen to 4 days in overcast spells
LVL_PCT, STRUCT_DROP, MIN_FRAMES, LVL_FLOOR = 50, 0.35, 12, 55
POS_TOL, MAX_STEP, MAX_BRACKET_SPREAD = 60.0, 45.0, 18.0
TRACK_KEEP, VEL_MAX = 10, 45.0
EDGE_TIERS = ((60, "CRITICAL: lock cannot measure upward"),
              (85, "WARN: lock upward range reduced"),
              (105, "WARN: background annulus clipped"))

# ---- TMAP & photometry parameters
TMAP_DAYS = (3, 6)           # try 3 days of clean brackets, widen to 6 if overcast
TMAP_NMAX = 240
PHOT_TAIL_BYTES = 35_000_000 # ~14+ days tail read of 316 MB CSV (< 0.5 s)
BAYES_MAX_N = 2000

HIST_FIELDS = ["day", "status", "cx", "cy", "step", "spread", "nbuckets", "vx", "vy", "epoch"]
DEFAULT_CRF = {"b": [0.55, 3.8], "g": [0.45, 3.8], "r": [0.36, 3.2]}


def log(msg):
    os.makedirs(STATE_DIR, exist_ok=True)
    line = f"{datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')} {msg}"
    with open(LOG, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=2)
    os.replace(tmp, path)


def append_csv(path, fields, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path) as fh:
            if fh.readline().strip().split(",") != fields:
                os.replace(path, path.replace(".csv", "_v1.csv"))
    new = not os.path.exists(path)
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


# ------------------------------------------------------------------ CRF & sky math
def crf_eval(v, a, g):
    """Empirical camera response function: encoded [0..255] -> relative linear radiance [0..1]."""
    x = np.clip(np.asarray(v, np.float64) / 255.0, 0.0, 1.0)
    c0 = (a / (1.0 + a)) ** g
    return (((x + a) / (1.0 + a)) ** g - c0) / (1.0 - c0)


def frame_epoch(path):
    return datetime.datetime.strptime(os.path.basename(path)[:15], "%Y%m%d_%H%M%S").replace(
        tzinfo=datetime.timezone.utc)


def _sky_design(h2, bx, by, r0=RING0, r1=RING1, top=TOP_MASK):
    """Precompute ring mask, design matrix A, evaluation matrix E, and ridge penalty."""
    yy, xx = np.mgrid[0:h2, 0:h2]
    rb = np.hypot(xx - bx, yy - by)
    ring = (rb >= r0) & (rb < r1) & (yy >= top)
    if ring.sum() < 400:
        ring = (rb >= max(65, r0 - 15)) & (rb < r1 + 15) & (yy >= top)
    ay, ax = np.where(ring)
    ur, vr = (ax - bx) / 100.0, (ay - by) / 100.0
    ue, ve = ((xx - bx) / 100.0).ravel(), ((yy - by) / 100.0).ravel()
    A = np.column_stack([np.ones_like(ur), ur, vr, ur * ur, ur * vr, vr * vr])
    E = np.column_stack([np.ones_like(ue), ue, ve, ue * ue, ue * ve, ve * ve])
    # Heavy ridge on quadratic terms whenever the upper ring is clipped by the top edge
    pen = RIDGE_QUAD if by < r1 + top else (2.0, 2.0, 2.0)
    R = np.diag([0.0, 0.0, 0.0, pen[0], pen[1], pen[2]])
    return yy, xx, rb, ring, A, E, R


def fit_sky(vals, A, E, R, shape):
    """Sigma-clipped ridge-regularized 2D quadratic sky background + robust residual MAD."""
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


def half_depth_centroid(dip, x_lo, y_lo, px, py, tol=POS_TOL):
    """Centroid of pixels above half-peak dip within tol of prior."""
    S = cv2.GaussianBlur(dip.astype(np.float32), (0, 0), 5)
    gy, gx = np.mgrid[0:S.shape[0], 0:S.shape[1]]
    near = np.hypot(gx + x_lo - px, gy + y_lo - py) <= tol
    if not near.any():
        return None
    peak = float(np.percentile(S[near], 99.5))
    if peak <= 0:
        return None
    m = near & (S >= 0.5 * peak) & (S > 0)
    if m.sum() < 30:
        return None
    w = S[m].astype(np.float64)
    return (float((gx[m] * w).sum() / w.sum()) + x_lo,
            float((gy[m] * w).sum() / w.sum()) + y_lo, peak)


# ------------------------------------------------------------------ state & velocity
def fit_velocity(track):
    if len(track) < 3:
        return [0.0, 0.0]
    t = np.array([datetime.datetime.fromisoformat(e).timestamp() / 86400.0 for e, _, _ in track])
    if t.max() - t.min() < 1.0:
        return [0.0, 0.0]
    t -= t.mean()
    vx = float(np.polyfit(t, [c for _, c, _ in track], 1)[0])
    vy = float(np.polyfit(t, [c for _, _, c in track], 1)[0])
    vmag = float(np.hypot(vx, vy))
    if vmag > VEL_MAX:
        vx, vy = vx * VEL_MAX / vmag, vy * VEL_MAX / vmag
    return [round(vx, 3), round(vy, 3)]


def read_state():
    try:
        s = json.load(open(STATE))
        if "track" in s:
            return s
    except Exception:
        pass
    return {"version": 3, "cx": None, "cy": None, "track": [], "velocity": [0.0, 0.0],
            "day": None, "fit_epoch": None, "fails": 0}


# ------------------------------------------------------------------ 1. position tracker
def _score_bracket(args):
    path, x_lo, y_lo, x_hi, y_hi, px, py = args
    im = cv2.imread(path)
    if im is None:
        return None
    sub = im[y_lo:y_hi, x_lo:x_hi, :3]
    if sub.size == 0:
        return None
    g = sub.astype(np.float32).mean(axis=2)
    lvl = float(np.median(g))
    if lvl < LVL_FLOOR or (g > 252).mean() > 0.05:
        return None
    ratio = g / np.maximum(cv2.GaussianBlur(g, (0, 0), 60), 1e-3)
    gy, gx = np.mgrid[0:g.shape[0], 0:g.shape[1]]
    outer = np.hypot(gx + x_lo - px, gy + y_lo - py) > 100
    struct = float(np.std(ratio[outer])) if outer.any() else 9.9
    return (lvl, struct, path, g.astype(np.float64))


def estimate_position(days, px, py):
    """Pin one centre across br0/br1/br2 (parallelized across WORKERS threads)."""
    y_lo, y_hi = max(TOP_MASK, int(py) - HALF), min(FULL_H, int(py) + HALF)
    x_lo, x_hi = max(0, int(px) - HALF), min(FULL_W, int(px) + HALF)
    gcx, gcy = px - x_lo, py - y_lo
    sh = (y_hi - y_lo, x_hi - x_lo)
    yy, xx = np.mgrid[0:sh[0], 0:sh[1]]
    rb = np.hypot(xx - gcx, yy - gcy)
    ring = (rb >= RING0) & (rb < RING1)
    if ring.sum() < 400:
        ring = (rb >= 70) & (rb < 130)
    ay, ax = np.where(ring)
    ur, vr = (ax - gcx) / 100.0, (ay - gcy) / 100.0
    ue, ve = ((xx - gcx) / 100.0).ravel(), ((yy - gcy) / 100.0).ravel()
    A = np.column_stack([np.ones_like(ur), ur, vr, ur * ur, ur * vr, vr * vr])
    E = np.column_stack([np.ones_like(ue), ue, ve, ue * ue, ue * ve, ve * ve])
    pen = RIDGE_QUAD if gcy < RING1 else (2.0, 2.0, 2.0)
    R = np.diag([0.0, 0.0, 0.0, pen[0], pen[1], pen[2]])

    norm, singles, stamps = [], [], []
    for bucket in ("br0", "br1", "br2"):
        files = [f for d in days for f in sorted(glob.glob(os.path.join(BRACKETS, f"{d}_*_{bucket}_*.png")))]
        if len(files) < MIN_FRAMES:
            continue
        tasks = [(f, x_lo, y_lo, x_hi, y_hi, px, py) for f in files]
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            cand = [r for r in ex.map(_score_bracket, tasks) if r is not None]
        if len(cand) < MIN_FRAMES:
            continue
        lvls = np.array([c[0] for c in cand])
        bright = [c for c in cand if c[0] >= np.percentile(lvls, LVL_PCT)] or cand
        bright.sort(key=lambda t: t[1])
        keep = bright[:max(MIN_FRAMES, int(len(bright) * (1 - STRUCT_DROP)))]
        rats = []
        for _, _, path, g in keep:
            try:
                stamps.append(frame_epoch(path).timestamp())
            except ValueError:
                pass
            bg, _ = fit_sky(g[ring], A, E, R, sh)
            if np.median(bg) >= 40:
                rats.append(g / np.maximum(bg, 1e-3))
        if len(rats) < MIN_FRAMES:
            continue
        T = np.median(np.stack(rats), axis=0)
        c = half_depth_centroid(1.0 - T, x_lo, y_lo, px, py)
        if c is None:
            continue
        singles.append((c[0], c[1]))
        norm.append((1.0 - T) / max(c[2], 1e-9))
    if len(norm) < 2:
        return None
    c = half_depth_centroid(np.mean(np.stack(norm), axis=0), x_lo, y_lo, px, py)
    if c is None:
        return None
    sx, sy = [s[0] for s in singles], [s[1] for s in singles]
    epoch = datetime.datetime.fromtimestamp(sum(stamps) / len(stamps), tz=datetime.timezone.utc)
    return dict(cx=c[0], cy=c[1], spread=float(max(max(sx) - min(sx), max(sy) - min(sy))),
                nbuckets=len(norm), epoch=epoch.isoformat(timespec="seconds"))


def run_night(day, state):
    vx, vy = state.get("velocity") or [0.0, 0.0]
    d_base = datetime.datetime.strptime(day, "%Y%m%d").replace(tzinfo=datetime.timezone.utc)
    if state.get("cx") is not None:
        dt = 1.0
        try:
            d0 = d_base - datetime.timedelta(days=1)
            dt = max((d0 + datetime.timedelta(hours=18)
                      - datetime.datetime.fromisoformat(state["fit_epoch"])).total_seconds() / 86400.0, 0.0)
        except (KeyError, TypeError, ValueError):
            pass
        px, py = state["cx"] + vx * dt, state["cy"] + vy * dt
        src = f"recursive+velocity({vx:+.2f},{vy:+.2f} px/d x {dt:.1f} d)"
    else:
        px, py = SEED
        src = "seed"

    for lim, msg in EDGE_TIERS:
        if py < lim:
            log(f"[{day}] EDGE {msg} (centre y={py:.0f}); ACTION: move camera UP a few mm")
            break

    est = None
    used_days = []
    for win_d in TRACK_DAYS:
        used_days = [(d_base - datetime.timedelta(days=k)).strftime("%Y%m%d") for k in range(1, win_d + 1)]
        log(f"[{day}] prior ({px:.1f},{py:.1f}) from {src}; window {used_days}")
        est = estimate_position(used_days, px, py)
        if est is not None:
            break

    def carry(reason):
        state["fails"] = state.get("fails", 0) + 1
        log(f"[{day}] CARRY-FORWARD: {reason} (fails={state['fails']})")
        return None

    if est is None:
        return carry("not enough clear frames even in widened window")
    step = float(np.hypot(est["cx"] - px, est["cy"] - py))
    est["step"] = step
    log(f"[{day}] centre ({est['cx']:.1f},{est['cy']:.1f}) step={step:.1f}px "
        f"bracket-spread={est['spread']:.1f}px n_buckets={est['nbuckets']}")
    if est["spread"] > MAX_BRACKET_SPREAD:
        return carry(f"bracket spread {est['spread']:.1f} > {MAX_BRACKET_SPREAD}px")
    if step > MAX_STEP:
        return carry(f"step {step:.1f} > {MAX_STEP}px")

    track = [t for t in (state.get("track") or []) if t[0] != est["epoch"]]
    track = (track + [[est["epoch"], round(est["cx"], 2), round(est["cy"], 2)]])[-TRACK_KEEP:]
    state.update(version=3, cx=round(est["cx"], 2), cy=round(est["cy"], 2), day=day,
                 fit_epoch=est["epoch"], track=track, velocity=fit_velocity(track), fails=0)
    log(f"[{day}] velocity ({state['velocity'][0]:+.2f},{state['velocity'][1]:+.2f}) px/day from {len(track)} nights")
    return est


# ------------------------------------------------------------------ 2. CRF + 3. Stan M2
def _read_photometry_tail(days_back=14):
    """Fast tail-read of spot_photometry.csv (clean, unclipped, gain=0 spot rows)."""
    if not os.path.exists(PHOT):
        return []
    with open(PHOT) as f:
        hdr = f.readline().strip().split(",")
    with open(PHOT, "rb") as f:
        f.seek(0, 2)
        sz = f.tell()
        f.seek(max(0, sz - PHOT_TAIL_BYTES))
        if sz > PHOT_TAIL_BYTES:
            f.readline()
        lines = f.read().decode("utf-8", errors="ignore").splitlines()
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_back)).isoformat()
    out = []
    for r in csv.DictReader(lines, fieldnames=hdr):
        if r.get("patch") != "spot" or r.get("gain") != "0" or r.get("ts_utc", "") < cutoff:
            continue
        try:
            bg, dk, fac = float(r["bg_fit"]), float(r["disk_med"]), float(r["facade_med"])
            exp, mad = float(r["exp_units"]), float(r["ann_resid_mad"])
            if float(r["clip_hi"]) < 0.005 and float(r["clip_lo"]) < 0.005 and mad < 2.0 and exp > 0 and 15 < bg < 248:
                out.append((r["ts_utc"], r["role"], r["ch"], bg, dk, fac, exp, mad))
        except (KeyError, ValueError, TypeError):
            pass
    return out


def fit_crf(clean_rows):
    """Fit 2-parameter CRF (a, gamma) per channel from same-tick br0/br1/br2 exposure ratios."""
    by_tick = collections.defaultdict(dict)
    for ts, role, ch, bg, dk, fac, exp, mad in clean_rows:
        if role in ("br0", "br1", "br2") and mad < 1.5:
            by_tick[(ts[:16], ch)][role] = (bg, exp)
    crf = {}
    for ch in "bgr":
        pairs = []
        for (_, c), d in by_tick.items():
            if c != ch:
                continue
            for k0, k1 in (("br0", "br1"), ("br1", "br2"), ("br0", "br2")):
                if k0 in d and k1 in d and d[k1][1] > d[k0][1] * 1.2 and d[k1][0] > d[k0][0] + 5:
                    pairs.append((d[k0][0], d[k1][0], d[k1][1] / d[k0][1]))
        if len(pairs) < 40:
            crf[ch] = list(DEFAULT_CRF[ch])
            continue
        arr = np.array(pairs)
        def loss(par):
            a, g = par
            if not (0.0 <= a <= 1.5 and 1.2 <= g <= 4.5):
                return 1e9
            r = np.log((crf_eval(arr[:, 1], a, g) / np.maximum(crf_eval(arr[:, 0], a, g), 1e-9)) / arr[:, 2])
            return float(np.mean(np.where(np.abs(r) < 0.05, 0.5 * r * r, 0.05 * (np.abs(r) - 0.025))))
        res = minimize(loss, DEFAULT_CRF[ch], method="Nelder-Mead")
        a, g = float(res.x[0]), float(res.x[1])
        err = float(np.median(np.abs(np.log((crf_eval(arr[:, 1], a, g) / crf_eval(arr[:, 0], a, g)) / arr[:, 2])))) * 100
        crf[ch] = [round(a, 4), round(g, 3)]
        log(f"[crf] ch={ch} n={len(pairs)} a={a:.4f} gamma={g:.3f} med_ratio_err={err:.2f}%")
    return crf


def fit_bayesian_m2(clean_rows, crf):
    """Run vectorized 3-channel Bayesian Stan M2 model on akdeniz -> posterior for flatfield."""
    if not os.path.exists(STAN_FILE):
        log("[bayes] spot_m2.stan missing; skipping Stan fit")
        return None
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).isoformat()
    ch_map = {"b": 1, "g": 2, "r": 3}
    ch_a, y_a, fac_a, sig_a = [], [], [], []
    for ts, role, ch, bg, dk, fac, exp, mad in clean_rows:
        if ts < cutoff or bg < 20 or ch not in ch_map:
            continue
        a, g = crf[ch]
        tau = exp * 1e-4
        Lbg = float(crf_eval(bg, a, g)) / tau
        Lsp = float(crf_eval(dk, a, g)) / tau
        Lfac = float(crf_eval(fac, a, g)) / tau
        if Lbg <= 0.1:
            continue
        y = Lsp / Lbg
        if not (0.75 < y < 1.10):
            continue
        dL = (float(crf_eval(min(bg + mad, 255), a, g)) - float(crf_eval(max(bg - mad, 0), a, g))) / (2.0 * tau)
        ch_a.append(ch_map[ch]); y_a.append(y)
        fac_a.append(min(Lfac / Lbg, 5.0)); sig_a.append(max(dL / Lbg, 1e-4))
    if len(y_a) < 120:
        log(f"[bayes] only {len(y_a)} clean rows in last 7d; skipping Stan fit")
        return None
    try:
        import cmdstanpy
        exe = os.path.join(STATE_DIR, "spot_m2")
        if os.path.exists(exe) and os.path.getmtime(exe) >= os.path.getmtime(STAN_FILE):
            mod = cmdstanpy.CmdStanModel(exe_file=exe)
        else:
            mod = cmdstanpy.CmdStanModel(stan_file=STAN_FILE)
            import shutil
            shutil.copy2(mod.exe_file, exe)
        step = max(1, len(y_a) // BAYES_MAX_N)
        data = dict(N=len(y_a[::step]), ch=ch_a[::step], y=y_a[::step],
                    fac_ratio=fac_a[::step], sig=sig_a[::step])
        fit = mod.sample(data=data, chains=4, parallel_chains=4, adapt_delta=0.95,
                         iter_warmup=500, iter_sampling=500, show_progress=False, show_console=False)
        s = fit.summary()
        rhat_max = float(s["R_hat"].max())
        diag_ok = "no problems detected" in fit.diagnose()
        out = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "n": data["N"], "rhat_max": round(rhat_max, 4), "diag_ok": diag_ok, "channels": {}}
        for i, ch in enumerate("bgr", 1):
            out["channels"][ch] = {
                "t": round(float(s.loc[f"t[{i}]", "50%"]), 5),
                "t_ci": [round(float(s.loc[f"t[{i}]", "5%"]), 5), round(float(s.loc[f"t[{i}]", "95%"]), 5)],
                "k_fac": round(float(s.loc[f"k_fac[{i}]", "50%"]), 5),
                "k_fac_ci": [round(float(s.loc[f"k_fac[{i}]", "5%"]), 5), round(float(s.loc[f"k_fac[{i}]", "95%"]), 5)],
                "sig_x": round(float(s.loc[f"sig_x[{i}]", "50%"]), 5),
                "nu": round(float(s.loc[f"nu[{i}]", "50%"]), 2),
            }
        log(f"[bayes] Stan N={data['N']} rhat={rhat_max:.4f} ok={diag_ok} "
            + " ".join(f"{c.upper()}:(t={out['channels'][c]['t']:.4f},k={out['channels'][c]['k_fac']:+.4f},sx={out['channels'][c]['sig_x']:.4f})"
                       for c in "bgr"))
        if rhat_max > 1.05:
            log("[bayes] WARN rhat_max > 1.05; refusing new Stan posterior")
            return None
        write_json_atomic(BAYES_OUT, out)
        return out
    except Exception as e:
        log(f"[bayes] Stan fit failed ({e}); reusing previous posterior if available")
        return None


# ------------------------------------------------------------------ 4. multi-bracket TMAP
def build_tmap(cx, cy, vx, vy, fit_epoch, crf, ref_day=None):
    """Build registered 2D transmittance map in CRF space from clean, unclipped brackets."""
    h = HALF
    x0f = int(round(min(max(cx - h, 0), FULL_W - 2 * h)))
    y0f = int(round(min(max(cy - h, 0), FULL_H - 2 * h)))
    bx, by = cx - x0f, cy - y0f
    yy, xx, rb, ring, A, E, R = _sky_design(2 * h, bx, by)
    core = (rb < R_CORE) & (yy >= TOP_MASK)
    disk = (rb < R_DISK) & (yy >= TOP_MASK)
    if ring.sum() < 400:
        return None, "ring cannot be seated"

    now_dt = (datetime.datetime.strptime(ref_day, "%Y%m%d").replace(tzinfo=datetime.timezone.utc)
              + datetime.timedelta(days=1) if ref_day else datetime.datetime.now(datetime.timezone.utc))

    def _one_frame(f):
        try:
            dtd = (frame_epoch(f) - fit_epoch).total_seconds() / 86400.0
        except ValueError:
            return None
        x0 = int(round(min(max(cx + vx * dtd - bx, 0), FULL_W - 2 * h)))
        y0 = int(round(min(max(cy + vy * dtd - by, 0), FULL_H - 2 * h)))
        im = cv2.imread(f)
        if im is None or y0 + 2 * h > im.shape[0] or x0 + 2 * h > im.shape[1]:
            return None
        win = im[y0:y0 + 2 * h, x0:x0 + 2 * h]
        res = {}
        for c, ch in enumerate("bgr"):
            raw = win[:, :, c].astype(np.float64)
            Lmed = float(np.median(raw[ring]))
            if Lmed < 45 or Lmed > 242 or (raw[ring | core] > 251).mean() > 0.01:
                continue
            # Clean-sky gate in encoded DN (shot noise is ~1-2 DN across all channels/levels)
            _, mad_raw = fit_sky(raw[ring], A, E, R, (2 * h, 2 * h))
            if mad_raw > 2.5:
                continue
            a, g_exp = crf[ch]
            lin = crf_eval(raw, a, g_exp)
            B, _ = fit_sky(lin[ring], A, E, R, (2 * h, 2 * h))
            if float(np.median(B[ring])) < 1e-4:
                continue
            res[c] = np.clip(lin / np.maximum(B, 1e-4), 0.5, 1.5).astype(np.float32)
        return res

    acc = {0: [], 1: [], 2: []}
    used_win = TMAP_DAYS[0]
    for win_d in TMAP_DAYS:
        used_win = win_d
        cutoff = (now_dt - datetime.timedelta(days=win_d)).strftime("%Y%m%d")
        hi_day = now_dt.strftime("%Y%m%d")
        fs = sorted(f for f in glob.glob(os.path.join(BRACKETS, "*_br*.png"))
                    if cutoff <= os.path.basename(f)[:8] <= hi_day
                    and "110000" <= os.path.basename(f)[9:15] <= "230000")
        if len(fs) > TMAP_NMAX:
            # Uniformly spaced indices across the ENTIRE window (never truncates newest frames!)
            idx = np.linspace(0, len(fs) - 1, TMAP_NMAX, dtype=int)
            fs = [fs[i] for i in idx]
        acc = {0: [], 1: [], 2: []}
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for r in ex.map(_one_frame, fs):
                if r:
                    for c, arr in r.items():
                        acc[c].append(arr)
        if min(len(acc[c]) for c in range(3)) >= 15:
            break

    counts = [len(acc[c]) for c in range(3)]
    n = min(counts)
    if n < 15:
        return None, f"only {counts} clean frames/channel over {used_win}d"
    T = np.stack([
        cv2.GaussianBlur(np.clip(np.median(np.stack(acc[c]), axis=0), 0.5, 1.05).astype(np.float32), (0, 0), 1.5)
        for c in range(3)
    ], axis=2)
    if not (0.80 < float(T.min()) < 0.995):
        return None, f"min_T={T.min():.3f} outside (0.80,0.995)"

    # Content centroid & core-to-disk scale (maps r<60 Stan M2 depth to r<25 core peak)
    D = cv2.GaussianBlur((1.0 - T.mean(axis=2)).astype(np.float32), (0, 0), 5)
    near = (rb <= 70) & (yy >= TOP_MASK)
    pk = float(np.percentile(D[near], 99.5))
    mx, my = bx, by
    m = near & (D >= 0.5 * pk) & (D > 0)
    if pk > 0.003 and m.sum() >= 30:
        w = D[m].astype(np.float64)
        mx, my = float((xx[m] * w).sum() / w.sum()), float((yy[m] * w).sum() / w.sum())
    core_depth, core_scale = {}, {}
    for c, ch in enumerate("bgr"):
        Dc = np.clip(1.0 - T[:, :, c], 0.0, 0.25)
        dc = float(np.median(Dc[core]))
        dd = max(float(np.median(Dc[disk])), 1e-4)
        core_depth[ch] = round(dc, 5)
        core_scale[ch] = round(min(max(dc / dd, 0.8), 2.5), 3)
    meta = {"x0": x0f, "y0": y0f, "half": h, "bx": round(bx, 1), "by": round(by, 1),
            "mx": round(mx, 1), "my": round(my, 1), "n": int(n), "n_ch": counts,
            "min_T": round(float(T.min()), 3), "core_depth": core_depth, "core_scale": core_scale}
    return (T, meta), None


# ------------------------------------------------------------------ 5. publish
def build_and_publish(state, push=True, ref_day=None):
    cx, cy = state.get("cx"), state.get("cy")
    if cx is None or not state.get("fit_epoch"):
        log("[publish] no position in state; skipping")
        return
    vx, vy = state.get("velocity") or [0.0, 0.0]
    fit_epoch = datetime.datetime.fromisoformat(state["fit_epoch"])

    clean_rows = _read_photometry_tail(days_back=14)
    crf = fit_crf(clean_rows)
    bayes = fit_bayesian_m2(clean_rows, crf)
    if bayes is None and os.path.exists(OUT_MODEL):
        try:
            bayes = json.load(open(OUT_MODEL)).get("bayes")
        except Exception:
            pass

    built, why = build_tmap(float(cx), float(cy), float(vx), float(vy), fit_epoch, crf, ref_day=ref_day)
    if built is None:
        log(f"[publish] NOT publishing ({why}); live keeps last good")
        return
    T, tmap = built
    np.save(OUT_TMAP, T)
    sha16 = hashlib.sha256(np.load(OUT_TMAP).tobytes()).hexdigest()[:16]
    model = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "schema": 3,
        "code_version": CODE_VERSION,
        "tmap_sha256_16": sha16,
        "source": "spotnight_bayes",
        "fit_day": state.get("day"),
        "fit_epoch": fit_epoch.isoformat(timespec="seconds"),
        "spots": [{"fx": round(float(cx) / FULL_W, 5), "fy": round(float(cy) / FULL_H, 5),
                   "amp_pct": round(100 * (1 - tmap["min_T"]), 2), "r_px": R_PX}],
        "velocity": [round(float(vx), 2), round(float(vy), 2)],
        "velocity_units": "px/day",
        "tmap": tmap,
        "crf": crf,
    }
    if bayes:
        model["bayes"] = bayes
    write_json_atomic(OUT_MODEL, model)
    log(f"[publish] built schema=3 centre=({cx},{cy}) v=({vx:+.2f},{vy:+.2f})/d "
        f"content=({tmap['mx']},{tmap['my']}) n_ch={tmap['n_ch']} min_T={tmap['min_T']} "
        f"core={tmap['core_depth']} bayes={'yes' if bayes else 'no'}")
    if not push:
        log("[publish] dry-run: nothing pushed")
        return
    try:
        for local, remote in ((OUT_TMAP, "monitor/spot_tmap.npy"), (OUT_MODEL, "monitor/spot_model.json")):
            subprocess.run(["scp", "-q", "-o", "BatchMode=yes", local, f"{PANDR}:{remote}.new"],
                           check=True, timeout=120)
        subprocess.run(["ssh", "-o", "BatchMode=yes", PANDR,
                        "mv monitor/spot_tmap.npy.new monitor/spot_tmap.npy && "
                        "mv monitor/spot_model.json.new monitor/spot_model.json"],
                       check=True, timeout=60)
        log("[publish] pushed atomically to pandr")
    except Exception as e:
        log(f"[publish] push FAILED ({e}); live keeps last good")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="YYYYMMDD")
    ap.add_argument("--replay", help="YYYYMMDD:YYYYMMDD, inclusive (tracker only)")
    ap.add_argument("--dry-run", action="store_true", help="build model/tmap locally, do not push")
    ap.add_argument("--skip-track", action="store_true", help="skip tracker, rebuild+publish from current state")
    ap.add_argument("--reset-state", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(BRACKETS) or len(glob.glob(os.path.join(BRACKETS, "*.png"))) < 50:
        log(f"PREFLIGHT FAILED: fewer than 50 bracket frames in {BRACKETS}")
        return 0
    if args.reset_state and os.path.exists(STATE):
        os.remove(STATE)
        log("state reset")

    state = read_state()
    if args.replay:
        a, b = (datetime.datetime.strptime(s, "%Y%m%d") for s in args.replay.split(":"))
        days = [(a + datetime.timedelta(days=k)).strftime("%Y%m%d") for k in range((b - a).days + 1)]
        for day in days:
            est = run_night(day, state)
            write_json_atomic(STATE, state)
            row = dict(day=day, status="ok" if est else "carry", cx=state.get("cx"), cy=state.get("cy"),
                       vx=state.get("velocity", [None, None])[0], vy=state.get("velocity", [None, None])[1],
                       epoch=state.get("fit_epoch"))
            if est:
                row.update(step=round(est["step"], 1), spread=round(est["spread"], 1), nbuckets=est["nbuckets"])
            append_csv(HISTORY, HIST_FIELDS, row)
        return 0

    day = args.day or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    if not args.skip_track:
        try:
            est = run_night(day, state)
            write_json_atomic(STATE, state)
            row = dict(day=day, status="ok" if est else "carry", cx=state.get("cx"), cy=state.get("cy"),
                       vx=state.get("velocity", [None, None])[0], vy=state.get("velocity", [None, None])[1],
                       epoch=state.get("fit_epoch"))
            if est:
                row.update(step=round(est["step"], 1), spread=round(est["spread"], 1), nbuckets=est["nbuckets"])
            append_csv(HISTORY, HIST_FIELDS, row)
        except Exception:
            log(f"[{day}] UNCAUGHT in tracker:\n{traceback.format_exc()}")

    try:
        build_and_publish(state, push=not args.dry_run, ref_day=args.day)
    except Exception:
        log(f"[{day}] UNCAUGHT in build_and_publish:\n{traceback.format_exc()}")

    with open(MARKER, "w") as fh:
        fh.write(datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
