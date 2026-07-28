#!/usr/bin/env python3
"""spotnight.py -- unsupervised nightly window-spot estimator.

Designed to run for months without supervision. Same code path is used for the
historical backfill: `--day YYYYMMDD` replays any past night, which is also the only
honest test of the autonomous behaviour.

    spotnight.py                     # tonight, for today
    spotnight.py --day 20260725      # replay one night
    spotnight.py --replay 20260721:20260727   # replay a range, in order
    spotnight.py --shadow            # compute + validate, never publish (default ON)

WHAT IT DOES, and why each piece exists (every one of these is a failure this project
actually hit, not a hypothetical):

  * RECURSIVE PRIOR. Tonight's prior is last night's posterior propagated by the measured
    drift increment. The first version anchored to a fixed BASE constant, which is fine
    for a week and wrong over months -- the blemish moved 120 px in eight days.
  * POSITION PINNED ACROSS BRACKETS. The blemish has one position; the brackets are the
    same instant through the same glass. Estimating it per bracket gave a ~60 px spread;
    pinning plus a half-depth centroid brought that to ~3 px.
  * BRIGHT-THEN-FLAT frame selection. Ranking on flatness alone selects uniform overcast,
    the flattest sky there is and precisely where the dip is at the noise floor. That bug
    produced stacks with a 0.01% peak and a fit that pinned at its bounds.
  * GAIN CLOSED IN DISPLAY SPACE. CLAHE amplifies the dip ~2.85x, scene-dependently, so
    any fixed linear-space gain under-corrects deep dips. Measured: residual proportional
    to input, after/before 0.17 rising to 0.335, r=+0.74.
  * ACCEPTANCE GATE WITH REVERT. Feeds a public site, so a regression must not ship.
    Silent quality decay is the real months-scale risk, more than any crash.
  * CARRY-FORWARD as a first-class path, not an exception: long overcast spells are normal.
  * CLEAN-SKY metrics measured OUTSIDE the footprint, so they cannot be circular.
  * FRAME-EDGE ALARM. The blemish drifts toward the top edge (~3 px/day, ~150 px margin).
    That is a hard failure, not a degradation, and no code fixes it -- it needs the camera
    moved. Better to shout early.

Exit status is deliberately 0 in almost all cases: a nightly that dies loudly breaks the
launchd job, and a stale-but-good parameter set is always better than a bad new one.
Never trust the exit code of a long remote command over the tunnel either -- assert on
the output artifacts instead (this writes a completion marker for that reason).
"""
import argparse
import csv
import datetime
import glob
import json
import os
import shutil
import sys
import traceback

import numpy as np

try:
    import cv2
except ImportError:                                     # pragma: no cover
    print("spotnight: cv2 unavailable", file=sys.stderr)
    sys.exit(0)


HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
BRACKETS = os.environ.get("SPOT_BRACKETS", os.path.join(HOME, "monitor/spot_lab/brackets"))
DRIFT_CSV = os.environ.get("SPOT_DRIFT", os.path.join(HOME, "monitor/drift.csv"))
STATE_DIR = os.environ.get("SPOT_STATE_DIR", os.path.join(HOME, "monitor/spot_state"))
STATE = os.path.join(STATE_DIR, "state.json")
HISTORY = os.path.join(STATE_DIR, "history.csv")
LOG = os.path.join(STATE_DIR, "spotnight.log")
MARKER = os.path.join(STATE_DIR, "last_run_ok")

# ---- geometry
WIN = 150                  # half-window for stacks and maps
R_FP = 90                  # correction footprint radius
R0, R1 = 110, 170          # background annulus
TOP_MASK = 20              # excluded top rows (lens shading / ISP edge)
FEATHER_FULL, FEATHER_ZERO = 70.0, 100.0

# ---- selection
WINDOW_DAYS = 2            # position + shape from the last 2 days (drift-current)
LVL_PCT = 50               # keep the brightest half...
STRUCT_DROP = 0.35         # ...then drop the cloudiest 35%
MIN_FRAMES = 12
LVL_FLOOR = 60

# ---- position filter
POS_TOL = 60.0             # search radius around the recursive prior, px
MAX_STEP = 45.0            # reject a nightly jump larger than this, px
MAX_BRACKET_SPREAD = 18.0  # per-bracket centroid disagreement that means "low confidence"

# ---- gain, closed in display space
G1, G2 = 1.0, 2.2
GAIN_CLAMP = (0.2, 6.0)

# ---- acceptance gate (env-overridable so a replay can be run cheaply, and so
#      thresholds can be retuned without editing code in production)
def _envf(name, default):
    try:
        return type(default)(os.environ[name])
    except (KeyError, ValueError):
        return default


ACCEPT_N = _envf("SPOT_ACCEPT_N", 20)             # clean-sky ticks to validate on
# Floors below are set from a measured sweep of 1375 ticks with the closed-loop gain:
# median removal 0.881, |after|>1.5% on 0.083, inversions 0-1. They allow real headroom
# while still catching a regression.
ACCEPT_MIN_REMOVAL = _envf("SPOT_MIN_REMOVAL", 0.70)   # median fraction of dip removed
ACCEPT_MAX_INVERSE = _envf("SPOT_MAX_INVERSE", 0)      # zero tolerance for inverse spots
ACCEPT_MAX_ABOVE = _envf("SPOT_MAX_ABOVE", 0.25)       # fraction allowed above |1.5%|
ACCEPT_REGRESS_TOL = _envf("SPOT_REGRESS_TOL", 0.08)   # allowed worsening vs known-good
FAIL_ESCALATE = _envf("SPOT_FAIL_ESCALATE", 3)         # consecutive fails before shouting
CV_THREADS = _envf("SPOT_CV_THREADS", 0)               # 0 = let OpenCV use all cores

# Clean-sky descriptors for the validation subset. CLEAN_MAX_RING_STRUCT was originally
# 1.2, calibrated on RAW frames and then applied to FUSED ones -- but CLAHE amplifies
# structure, so the fused 5th percentile is 1.94 and the threshold rejected every tick,
# leaving the nightly with nothing to validate on. 3.0 is the measured p25-p50 band.
CLEAN_MIN_LVL = _envf("SPOT_CLEAN_MIN_LVL", 130.0)
CLEAN_MAX_RING_STRUCT = _envf("SPOT_CLEAN_MAX_RING", 3.0)
# Cheap pre-filter on the RAW frame, so hopeless candidates are rejected before paying for
# four 4K fusions apiece. Scanning by fuse-then-discard cost 76 min for one night.
RAW_MIN_LVL = _envf("SPOT_RAW_MIN_LVL", 120.0)
RAW_MAX_STRUCT = _envf("SPOT_RAW_MAX_STRUCT", 1.5)
PLAUSIBLE_DIP = (1.0, 9.0)  # a "dip" outside this is scene structure, not the blemish

# OpenCV threading is a real speedup for Mertens fusion (~12x on this box), so DO NOT pin
# it to 1 in a single-process job. Pin it only when running a multi-process pool, where
# N processes x ncores threads oversubscribe and thrash. SPOT_CV_THREADS=1 does that.
if CV_THREADS:
    cv2.setNumThreads(int(CV_THREADS))


def log(msg):
    os.makedirs(STATE_DIR, exist_ok=True)
    line = f"{datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')} {msg}"
    with open(LOG, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


# ----------------------------------------------------------------- image helpers
def load3(path):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    return img[:, :, :3] if (img.ndim == 3 and img.shape[2] > 3) else img


BG_HALF = 230              # local window for background evaluation


def quad_bg(ch, cx, cy, r0=R0, r1=R1, top=0):
    """Robust 2D-quadratic background from an annulus.

    The fit uses annulus samples only; the EVALUATION is confined to a local window
    around the spot. Evaluating over a whole 4K frame builds an 8.3M x 6 design matrix
    (~400 MB) and, called ~13x per tick, cost 346 s/tick -- 47x slower than necessary,
    for numerically identical results (nothing outside the window is ever corrected).
    """
    H, W = ch.shape
    y0 = max(top, int(cy) - BG_HALF); y1 = min(H, int(cy) + BG_HALF)
    x0 = max(0, int(cx) - BG_HALF);   x1 = min(W, int(cx) + BG_HALF)
    gy, gx = np.mgrid[y0:y1, x0:x1]
    sub = ch[y0:y1, x0:x1]
    rr = np.hypot(gx - cx, gy - cy)
    m = (rr >= r0) & (rr < r1) & (gy >= top)
    if m.sum() < 150:
        return None
    un, vn = (gx[m] - cx) / 100.0, (gy[m] - cy) / 100.0
    A = np.column_stack([np.ones(un.size), un, vn, un ** 2, un * vn, vn ** 2])
    vals = sub[m].astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
    res = vals - A @ coef
    mad = np.median(np.abs(res - np.median(res))) + 1e-9
    keep = np.abs(res) < 4 * 1.4826 * mad
    if keep.sum() > 100:
        coef, *_ = np.linalg.lstsq(A[keep], vals[keep], rcond=None)
    un2, vn2 = (gx - cx) / 100.0, (gy - cy) / 100.0
    bg = (coef[0] + coef[1] * un2 + coef[2] * vn2 + coef[3] * un2 ** 2
          + coef[4] * un2 * vn2 + coef[5] * vn2 ** 2)
    out = ch.astype(np.float64).copy()
    out[y0:y1, x0:x1] = bg
    return out


def half_depth_centroid(dip, x_lo, y_lo, px, py, tol=POS_TOL):
    """Centroid of the pixels below half depth. Argmin chases single-pixel noise and any
    residual background tilt; a centroid averages over the whole footprint."""
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
            float((gy[m] * w).sum() / w.sum()) + y_lo,
            peak, int(m.sum()))


# ----------------------------------------------------------------- state
def default_state():
    return dict(version=2, cx=None, cy=None, prev=None, drift=[0.0, 0.0], day=None,
                good=None, fails=0, history=[])


def read_state():
    try:
        s = json.load(open(STATE))
        for k, v in default_state().items():
            s.setdefault(k, v)
        return s
    except Exception:
        return default_state()


def write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1)
    os.replace(tmp, path)


def drift_by_day():
    out = {}
    try:
        for r in csv.DictReader(open(DRIFT_CSV)):
            if float(r.get("resp", 0)) < 0.03:
                continue
            out.setdefault(r["ts_utc"][:10].replace("-", ""), []).append(
                (float(r["dx"]), float(r["dy"])))
    except Exception as e:
        log(f"WARN drift.csv unreadable ({e}); treating drift as unknown")
        return {}
    return {k: (float(np.median([a for a, _ in v])), float(np.median([b for _, b in v])))
            for k, v in out.items()}


# ----------------------------------------------------------------- selection
def select_frames(days, bucket, px, py):
    """Bright first, then flattest. Returns [(level, struct, path, sub)]."""
    y_lo = max(TOP_MASK, int(py) - WIN)
    x_lo = max(0, int(px) - WIN)
    cand = []
    for day in days:
        for f in sorted(glob.glob(os.path.join(BRACKETS, f"{day}_*_{bucket}_*.png"))):
            img = load3(f)
            if img is None:
                continue
            sub = img[y_lo:int(py) + WIN, x_lo:int(px) + WIN, :]
            if sub.size == 0:
                continue
            g = sub.astype(np.float32).mean(axis=2)
            lvl = float(np.median(g))
            if lvl < LVL_FLOOR or (g > 252).mean() > 0.05:
                continue
            ratio = g / np.maximum(cv2.GaussianBlur(g, (0, 0), 60), 1e-3)
            gy, gx = np.mgrid[0:g.shape[0], 0:g.shape[1]]
            outer = np.hypot(gx + x_lo - px, gy + y_lo - py) > 100
            struct = float(np.std(ratio[outer])) if outer.any() else 9.9
            cand.append((lvl, struct, f, sub.astype(np.float32)))
    if len(cand) < MIN_FRAMES:
        return [], x_lo, y_lo
    lvls = np.array([c[0] for c in cand])
    bright = [c for c in cand if c[0] >= np.percentile(lvls, LVL_PCT)] or cand
    bright.sort(key=lambda t: t[1])
    keep = bright[:max(MIN_FRAMES, int(len(bright) * (1 - STRUCT_DROP)))]
    return keep, x_lo, y_lo


def estimate_position(days, px, py):
    """One centre, pinned across all brackets. Each bracket's dip is normalised by its own
    peak before combining, because depth is exposure-dependent but shape is not. Also
    returns the MEAN EPOCH of the frames used: an estimate carries its data's epoch, and
    velocity must be computed against it (review 2026-07-28, finding 3)."""
    norm, singles, geom = [], [], None
    stamps = []
    for bucket in ("br0", "br1", "br2"):
        keep, x_lo, y_lo = select_frames(days, bucket, px, py)
        if not keep:
            continue
        for _, _, path, _ in keep:
            try:
                b = os.path.basename(path)
                stamps.append(datetime.datetime.strptime(b[:15], "%Y%m%d_%H%M%S")
                              .replace(tzinfo=datetime.timezone.utc).timestamp())
            except Exception:
                pass
        gcx, gcy = px - x_lo, py - y_lo
        rats = []
        for _, _, _, sub in keep:
            g = sub.mean(axis=2).astype(np.float64)
            bg = quad_bg(g, gcx, gcy)
            if bg is None or np.median(bg) < 40:
                continue
            rats.append(g / np.maximum(bg, 1e-3))
        if len(rats) < MIN_FRAMES:
            continue
        T = np.median(np.stack(rats), axis=0)
        c = half_depth_centroid(1.0 - T, x_lo, y_lo, px, py)
        if c is None:
            continue
        if geom is None:
            geom = (x_lo, y_lo, T.shape)
        elif (x_lo, y_lo) != geom[:2] or T.shape != geom[2]:
            continue
        singles.append((bucket, c[0], c[1]))
        norm.append((1.0 - T) / max(c[2], 1e-9))
    if len(norm) < 2 or geom is None:
        return None
    comb = np.mean(np.stack(norm), axis=0)
    c = half_depth_centroid(comb, geom[0], geom[1], px, py)
    if c is None:
        return None
    sx = [s[1] for s in singles]
    sy = [s[2] for s in singles]
    spread = float(max(max(sx) - min(sx), max(sy) - min(sy)))
    epoch = (datetime.datetime.fromtimestamp(sum(stamps) / len(stamps),
                                             tz=datetime.timezone.utc)
             .isoformat(timespec="seconds")) if stamps else None
    return dict(cx=c[0], cy=c[1], spread=spread, nbuckets=len(norm), epoch=epoch,
                brackets={s[0]: [round(s[1], 1), round(s[2], 1)] for s in singles})


def build_maps(days, cx, cy, outdir):
    """Per (bracket, channel) empirical transmittance maps at the pinned centre."""
    os.makedirs(outdir, exist_ok=True)
    meta = {}
    for bucket in ("br0", "br1", "br2"):
        keep, x_lo, y_lo = select_frames(days, bucket, cx, cy)
        if not keep:
            continue
        gcx, gcy = cx - x_lo, cy - y_lo
        for ci, chn in enumerate("bgr"):
            rats = []
            for _, _, _, sub in keep:
                ch = sub[:, :, ci].astype(np.float64)
                bg = quad_bg(ch, gcx, gcy)
                if bg is None or np.median(bg) < 40:
                    continue
                rats.append(ch / np.maximum(bg, 1e-3))
            if len(rats) < MIN_FRAMES:
                continue
            T = np.median(np.stack(rats), axis=0).astype(np.float32)
            S = cv2.GaussianBlur(T, (0, 0), 5)
            gy, gx = np.mgrid[0:S.shape[0], 0:S.shape[1]]
            rr = np.hypot(gx - gcx, gy - gcy)
            peak = (1 - float(np.percentile(S[rr < 25], 5)))
            noise = float(np.std(T[rr > 110])) if (rr > 110).any() else 9.9
            if peak <= 0.001:
                continue
            np.save(os.path.join(outdir, f"{bucket}_{chn}.npy"), T)
            meta[f"{bucket}_{chn}"] = dict(n=len(rats), x_lo=x_lo, y_lo=y_lo,
                                           peak=round(peak, 5), noise=round(noise, 5))
    return meta


# ----------------------------------------------------------------- correction
class Corrector:
    """Applies the correction and closes the gain loop in display space."""

    def __init__(self, mapdir, meta, cx, cy):
        self.mapdir, self.meta, self.cx, self.cy = mapdir, meta, cx, cy
        self._cache = {}

    def shape(self, bucket, chn, H, W):
        key = (bucket, chn, H, W)
        if key in self._cache:
            return self._cache[key]
        m = self.meta.get(f"{bucket}_{chn}")
        if m is None:
            return None
        T = np.load(os.path.join(self.mapdir, f"{bucket}_{chn}.npy")).astype(np.float64)
        S = np.zeros((H, W))
        hh, ww = T.shape
        y1, x1 = min(H, m["y_lo"] + hh), min(W, m["x_lo"] + ww)
        S[m["y_lo"]:y1, m["x_lo"]:x1] = np.clip(
            (1.0 - T[:y1 - m["y_lo"], :x1 - m["x_lo"]]) / m["peak"], 0, 1)
        S = cv2.GaussianBlur(S.astype(np.float32), (0, 0), 2).astype(np.float64)
        self._cache[key] = (S, m["peak"])
        return self._cache[key]

    def apply(self, imgs, gain):
        H, W = imgs[0].shape[:2]
        gy, gx = np.mgrid[0:H, 0:W]
        rr = np.hypot(gx - self.cx, gy - self.cy)
        feather = np.clip((FEATHER_ZERO - rr) / (FEATHER_ZERO - FEATHER_FULL), 0, 1)
        out = []
        for bi, bucket in enumerate(("br0", "br1", "br2")):
            o = imgs[bi].astype(np.float32).copy()
            for ci, chn in enumerate("bgr"):
                sh = self.shape(bucket, chn, H, W)
                if sh is None:
                    continue
                S, peak = sh
                ch = imgs[bi][:, :, ci].astype(np.float64)
                bg = quad_bg(ch, self.cx, self.cy, top=TOP_MASK)
                if bg is None:
                    continue
                T = np.clip(1.0 - gain * peak * S, 0.55, 1.0)
                Tf = 1.0 - feather * (1.0 - T)
                # frequency-split cap: an inverse bright spot is a low-frequency object,
                # so cap the smooth component at local sky and pass the grain through
                # (the old pixelwise cap flattened the noise's upper tail -- visibly
                # 'quiet' disc after enhancement)
                raw = ch / np.maximum(Tf, 1e-6)
                low = cv2.GaussianBlur(raw.astype(np.float32), (0, 0), 8).astype(np.float64)
                lowo = cv2.GaussianBlur(ch.astype(np.float32), (0, 0), 8).astype(np.float64)
                o[:, :, ci] = np.clip(np.minimum(low, np.maximum(bg, lowo))
                                      + (raw - low), 0, 255)
            out.append(o.astype(np.uint8))
        return out


def fuse(imgs, enhance):
    f = cv2.createMergeMertens().process([np.ascontiguousarray(i) for i in imgs])
    return enhance(np.clip(f * 255.0, 0, 255).astype(np.uint8))


def fused_dip(f, cx, cy):
    g = f.astype(np.float64).mean(axis=2)
    bg = quad_bg(g, cx, cy, top=TOP_MASK)
    if bg is None:
        return float("nan")
    gy, gx = np.mgrid[0:g.shape[0], 0:g.shape[1]]
    disk = np.hypot(gx - cx, gy - cy) < 45
    den = float(np.median(bg[disk]))
    if den < 8.0:                       # night: fused frame is ~black, dip undefined
        return float("nan")
    return (1 - float(np.median(g[disk])) / den) * 100


def clean_sky(f, cx, cy):
    """Level and ring structure, both measured OUTSIDE the footprint so they cannot be
    circular with respect to the dip we are trying to judge."""
    g = f.astype(np.float32).mean(axis=2)
    ratio = g / np.maximum(cv2.GaussianBlur(g, (0, 0), 60), 1e-3)
    gy, gx = np.mgrid[0:g.shape[0], 0:g.shape[1]]
    rr = np.hypot(gx - cx, gy - cy)
    m = (rr >= R0) & (rr < 200) & (gy >= TOP_MASK)
    return float(np.median(g)), (float(np.std(ratio[m])) * 100 if m.any() else 99.0)


def solve_gain(corr, imgs, enhance, cx, cy):
    """Adaptive secant on the FUSED dip, one refinement. CLAHE amplifies the dip
    scene-dependently, so the loop closes where it is judged; with the frequency-split
    cap the response can be steep on bright ticks, so the second probe is chosen on the
    other side of the root and the estimate is refined once."""
    def d_at(g):
        return fused_dip(fuse(corr.apply(imgs, g), enhance), cx, cy)
    d1 = d_at(G1)
    g2 = G2 if (np.isfinite(d1) and d1 > 0) else 0.4
    d2 = d_at(g2)
    def secant(ga, da, gb, db):
        if not (np.isfinite(da) and np.isfinite(db)) or abs(da - db) < 1e-6:
            return ga
        return ga + (gb - ga) * da / (da - db)
    g = float(min(max(secant(G1, d1, g2, d2), GAIN_CLAMP[0]), GAIN_CLAMP[1]))
    d3 = d_at(g)
    pts = [(G1, d1), (g2, d2), (g, d3)]
    pts = sorted([p_ for p_ in pts if np.isfinite(p_[1])], key=lambda t: abs(t[1]))
    if len(pts) >= 2 and abs(pts[0][1]) > 0.25:
        g = float(min(max(secant(pts[0][0], pts[0][1], pts[1][0], pts[1][1]),
                          GAIN_CLAMP[0]), GAIN_CLAMP[1]))
    return g, d1, d2


# ----------------------------------------------------------------- acceptance
def raw_is_promising(img, cx, cy):
    """Cheap raw-frame gate: bright enough to carry signal (the dip is multiplicative) and
    not obviously cloud-structured. Avoids fusing candidates that cannot pass."""
    g = img.astype(np.float32).mean(axis=2)
    y0 = max(TOP_MASK, int(cy) - WIN); y1 = min(g.shape[0], int(cy) + WIN)
    x0 = max(0, int(cx) - WIN);        x1 = min(g.shape[1], int(cx) + WIN)
    sub = g[y0:y1, x0:x1]
    if sub.size == 0 or float(np.median(sub)) < RAW_MIN_LVL or (sub > 252).mean() > 0.05:
        return False
    ratio = sub / np.maximum(cv2.GaussianBlur(sub, (0, 0), 60), 1e-3)
    gy, gx = np.mgrid[y0:y1, x0:x1]
    outer = np.hypot(gx - cx, gy - cy) > 100
    if not outer.any():
        return False
    return float(np.std(ratio[outer])) * 100 < RAW_MAX_STRUCT


def acceptance(days, corr, cx, cy, enhance, limit=ACCEPT_N):
    """Validate on clean-sky ticks only, and report the tail rather than the median."""
    stems = {}
    for day in days:
        for f in glob.glob(os.path.join(BRACKETS, f"{day}_*.png")):
            b = os.path.basename(f)
            stems.setdefault(b[:15], {})[b[16:19]] = f
    # Out-of-sample preference (review 2026-07-28): validate on the RUN DAY's ticks
    # first; frames from the map-building days are only used to top up, and the borrowed
    # fraction is recorded so a not-fully-out-of-sample gate is visible in the log.
    run_day = days[0]
    triples = sorted((k for k, v in stems.items() if len(v) == 3),
                     key=lambda k: (k[:8] != run_day, k), reverse=False)
    triples = [k for k in triples if k[:8] == run_day] +               list(reversed([k for k in triples if k[:8] != run_day]))
    rows, gains = [], []
    borrowed = 0
    for stem in triples:
        if len(rows) >= limit:
            break
        probe = load3(stems[stem]["br1"])
        if probe is None or not raw_is_promising(probe, cx, cy):
            continue
        imgs = [load3(stems[stem][b]) for b in ("br0", "br1", "br2")]
        if any(i is None for i in imgs):
            continue
        fb = fuse(imgs, enhance)
        lvl, ring = clean_sky(fb, cx, cy)
        if lvl < CLEAN_MIN_LVL or ring > CLEAN_MAX_RING_STRUCT:
            continue
        d0 = fused_dip(fb, cx, cy)
        if not np.isfinite(d0) or not (PLAUSIBLE_DIP[0] <= d0 <= PLAUSIBLE_DIP[1]):
            continue
        g, _, _ = solve_gain(corr, imgs, enhance, cx, cy)
        d1 = fused_dip(fuse(corr.apply(imgs, g), enhance), cx, cy)
        if not np.isfinite(d1):
            continue
        rows.append((stem, d0, d1, g))
        gains.append(g)
        if stem[:8] != run_day:
            borrowed += 1
    if not rows:
        return None
    b = np.array([r[1] for r in rows])
    a = np.array([r[2] for r in rows])
    return dict(n=len(rows), borrowed=borrowed,
                removal=round(float(1 - np.median(a / np.maximum(b, 1e-6))), 4),
                before_med=round(float(np.median(b)), 3),
                after_med=round(float(np.median(a)), 3),
                above15=round(float((np.abs(a) > 1.5).mean()), 4),
                inverse=int((a < -1.0).sum()),
                worst=round(float(a.min()), 3),
                gain_med=round(float(np.median(gains)), 3))


def gate(acc, good):
    """Absolute floors, plus a no-regression check against last-known-good."""
    if acc is None:
        return False, "no clean-sky validation ticks"
    if acc["inverse"] > ACCEPT_MAX_INVERSE:
        return False, f"{acc['inverse']} inverse bright spots"
    if acc["removal"] < ACCEPT_MIN_REMOVAL:
        return False, f"removal {acc['removal']:.2f} < {ACCEPT_MIN_REMOVAL}"
    if acc["above15"] > ACCEPT_MAX_ABOVE:
        return False, f"|after|>1.5% on {acc['above15']:.0%} > {ACCEPT_MAX_ABOVE:.0%}"
    if good:
        # Baseline is a rolling median of recent accepted nights, not the single best one.
        # Against one high-water mark, a perfectly good night (removal 0.85, zero
        # inversions) gets refused for being worse than an unusually good one (0.96) --
        # observed twice in a six-night replay.
        recent = [r for r in (good.get("recent") or []) if r is not None]
        base = float(np.median(recent)) if recent else good.get("removal")
        if base is not None and acc["removal"] < base - ACCEPT_REGRESS_TOL:
            return False, (f"regression: removal {acc['removal']:.2f} vs "
                           f"rolling baseline {base:.2f} (n={len(recent) or 1})")
    return True, "ok"


# ----------------------------------------------------------------- nightly
def preflight():
    problems = []
    if not os.path.isdir(BRACKETS):
        problems.append(f"no bracket dir {BRACKETS}")
    elif len(glob.glob(os.path.join(BRACKETS, "*.png"))) < 50:
        problems.append("fewer than 50 bracket frames on disk")
    if not os.path.exists(DRIFT_CSV):
        problems.append("no drift.csv")
    try:
        free_gb = shutil.disk_usage(STATE_DIR if os.path.isdir(STATE_DIR) else HOME).free / 1e9
        if free_gb < 5:
            problems.append(f"only {free_gb:.1f} GB free")
    except Exception:
        pass
    return problems


def run_night(day, state, shadow=True):
    days = [(datetime.datetime.strptime(day, "%Y%m%d")
             - datetime.timedelta(days=k)).strftime("%Y%m%d")
            for k in range(1, WINDOW_DAYS + 1)]
    drift = drift_by_day()

    # --- recursive prior: last posterior, propagated by the measured drift increment
    if state.get("cx") is not None:
        d_then = state.get("drift", [0.0, 0.0])
        d_now = drift.get(day) or drift.get(days[0]) or d_then
        prev = state.get("prev")           # the centre before last, if we have one
        if prev is not None:
            # Constant-velocity extrapolation of the feature's own track, in true px/day:
            # divide the centre step by the ELAPSED TIME BETWEEN DATA EPOCHS, not by an
            # assumed one-night step -- gaps, replays and delayed jobs otherwise corrupt
            # the velocity (review 2026-07-28, finding 3).
            dt_days = 1.0
            try:
                e1 = datetime.datetime.fromisoformat(state.get("fit_epoch"))
                e0 = datetime.datetime.fromisoformat(state.get("prev_epoch"))
                dt_days = max((e1 - e0).total_seconds() / 86400.0, 0.25)
            except Exception:
                pass
            vx = (state["cx"] - prev[0]) / dt_days
            vy = (state["cy"] - prev[1]) / dt_days
            vmag = float(np.hypot(vx, vy))
            if vmag > MAX_STEP:            # never extrapolate a wild step
                vx, vy = vx * MAX_STEP / vmag, vy * MAX_STEP / vmag
            px, py = state["cx"] + vx, state["cy"] + vy
            prior_src = f"recursive+velocity({vx:+.1f},{vy:+.1f})"
        else:
            px = state["cx"] + (d_now[0] - d_then[0])
            py = state["cy"] + (d_now[1] - d_then[1])
            prior_src = "recursive+drift"
    else:
        seed = json.loads(os.environ.get("SPOT_SEED", "[1184,176]"))
        d_now = drift.get(day) or drift.get(days[0]) or [0.0, 0.0]
        px, py = seed[0] + d_now[0], seed[1] + d_now[1]
        prior_src = "seed"
    log(f"[{day}] prior ({px:.1f},{py:.1f}) from {prior_src}; window {days}")

    # --- frame-edge alarm: the hard failure over months
    if py - R1 < TOP_MASK:
        log(f"[{day}] ALARM annulus cannot be seated: centre y={py:.0f}, needs "
            f"{R1 + TOP_MASK}. Background is extrapolated; MOVE THE CAMERA UP a few mm "
            f"(blemish is cm from the lens, skyline at infinity, so translation moves it "
            f"~100 px per ~4 mm without recomposing the view).")

    est = estimate_position(days, px, py)
    if est is None:
        state["fails"] = state.get("fails", 0) + 1
        log(f"[{day}] CARRY-FORWARD: not enough clear frames to estimate a position "
            f"(fails={state['fails']})")
        return state, None, None
    step = float(np.hypot(est["cx"] - px, est["cy"] - py))
    log(f"[{day}] centre ({est['cx']:.1f},{est['cy']:.1f}) step={step:.1f}px "
        f"bracket-spread={est['spread']:.1f}px n_buckets={est['nbuckets']}")

    if est["spread"] > MAX_BRACKET_SPREAD:
        state["fails"] = state.get("fails", 0) + 1
        log(f"[{day}] CARRY-FORWARD: bracket spread {est['spread']:.1f} > "
            f"{MAX_BRACKET_SPREAD}px, position low-confidence (fails={state['fails']})")
        return state, None, None
    if step > MAX_STEP:
        state["fails"] = state.get("fails", 0) + 1
        log(f"[{day}] CARRY-FORWARD: step {step:.1f} > {MAX_STEP}px, implausible jump "
            f"(fails={state['fails']})")
        return state, None, None

    cx, cy = est["cx"], est["cy"]
    mapdir = os.path.join(STATE_DIR, "maps", day)
    meta = build_maps(days, cx, cy, mapdir)
    if len(meta) < 3:
        state["fails"] = state.get("fails", 0) + 1
        log(f"[{day}] CARRY-FORWARD: only {len(meta)} transmittance maps built "
            f"(fails={state['fails']})")
        return state, None, None
    log(f"[{day}] built {len(meta)} maps; peaks " + ", ".join(
        f"{k}={v['peak']*100:.2f}%" for k, v in sorted(meta.items())[:3]) + " ...")

    sys.path.insert(0, os.path.join(HOME, "monitor"))
    try:
        from hdrfuse import enhance
    except Exception as e:
        log(f"[{day}] CARRY-FORWARD: hdrfuse unavailable ({e})")
        return state, None, None

    corr = Corrector(mapdir, meta, cx, cy)
    acc = acceptance([day] + days, corr, cx, cy, enhance)
    ok, why = gate(acc, state.get("good"))
    if acc:
        log(f"[{day}] acceptance n={acc['n']} (borrowed={acc.get('borrowed', 0)}) "
            f"before={acc['before_med']:.2f}% after={acc['after_med']:.2f}% "
            f"|after|>1.5%={acc['above15']:.0%} inverse={acc['inverse']} "
            f"worst={acc['worst']:.2f}% gain_med={acc['gain_med']:.2f}")

    if not ok:
        state["fails"] = state.get("fails", 0) + 1
        # Position and correction quality are SEPARATE judgements. The centre passed its
        # own sanity checks (bracket spread, step size) above, so it is recorded even when
        # the correction is refused -- otherwise a single bad night strands the recursive
        # prior on a stale seed and every subsequent night inherits the error. Only the
        # published parameters and `good` depend on the acceptance gate.
        state.update(prev=[state.get("cx"), state.get("cy")] if state.get("cx") is not None else None,
                     prev_epoch=state.get("fit_epoch"), fit_epoch=est.get("epoch"),
                     cx=round(cx, 2), cy=round(cy, 2), day=day,
                     drift=list(drift.get(day) or drift.get(days[0]) or state.get("drift")))
        log(f"[{day}] REJECTED for correction, position RETAINED ({cx:.1f},{cy:.1f}): "
            f"{why} (fails={state['fails']})")
        if state["fails"] >= FAIL_ESCALATE:
            log(f"[{day}] ESCALATE: {state['fails']} consecutive failures -- "
                f"correction parameters are stale and need a human")
        return state, None, acc

    # --- accept
    recent = list(((state.get("good") or {}).get("recent")) or [])
    recent.append(acc["removal"])
    recent = recent[-5:]                    # rolling window of accepted nights
    state.update(prev=[state.get("cx"), state.get("cy")] if state.get("cx") is not None else None,
                 prev_epoch=state.get("fit_epoch"), fit_epoch=est.get("epoch"),
                 cx=round(cx, 2), cy=round(cy, 2), day=day, fails=0,
                 drift=list(drift.get(day) or drift.get(days[0]) or state.get("drift")),
                 good=dict(day=day, removal=acc["removal"], gain=acc["gain_med"],
                           mapdir=mapdir, meta=meta, recent=recent))
    params = dict(updated=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                  day=day, cx=round(cx, 2), cy=round(cy, 2), gain=acc["gain_med"],
                  r_fp=R_FP, feather=[FEATHER_FULL, FEATHER_ZERO], mapdir=mapdir,
                  maps=meta, acceptance=acc, shadow=bool(shadow))
    write_json_atomic(os.path.join(STATE_DIR, "spot_params.json"), params)
    log(f"[{day}] ACCEPTED{' (shadow: nothing served)' if shadow else ''} -> spot_params.json")
    return state, params, acc


def append_history(day, state, acc, accepted):
    os.makedirs(STATE_DIR, exist_ok=True)
    new = not os.path.exists(HISTORY)
    with open(HISTORY, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["day", "accepted", "cx", "cy", "removal", "before_med",
                        "after_med", "above15", "inverse", "worst", "gain_med", "fails"])
        w.writerow([day, int(accepted), state.get("cx"), state.get("cy"),
                    (acc or {}).get("removal"), (acc or {}).get("before_med"),
                    (acc or {}).get("after_med"), (acc or {}).get("above15"),
                    (acc or {}).get("inverse"), (acc or {}).get("worst"),
                    (acc or {}).get("gain_med"), state.get("fails")])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--replay", help="YYYYMMDD:YYYYMMDD, inclusive, in order")
    ap.add_argument("--shadow", action="store_true",
                    help="explicit no-op: shadow mode is the default; kept so the "
                         "documented invocation works (review finding 10)")
    ap.add_argument("--publish", action="store_true",
                    help="clear the shadow flag in spot_params.json (does NOT itself wire "
                         "flatfield.py -- that is a separate, deliberate change)")
    ap.add_argument("--reset-state", action="store_true")
    args = ap.parse_args()

    problems = preflight()
    if problems:
        log("PREFLIGHT FAILED, no update: " + "; ".join(problems))
        return 0
    if args.reset_state and os.path.exists(STATE):
        os.remove(STATE)
        log("state reset")

    if args.replay:
        a, b = args.replay.split(":")
        d0 = datetime.datetime.strptime(a, "%Y%m%d")
        d1 = datetime.datetime.strptime(b, "%Y%m%d")
        days = [(d0 + datetime.timedelta(days=k)).strftime("%Y%m%d")
                for k in range((d1 - d0).days + 1)]
    else:
        days = [args.day or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")]

    state = read_state()
    for day in days:
        try:
            state, params, acc = run_night(day, state, shadow=not args.publish)
            write_json_atomic(STATE, state)
            append_history(day, state, acc, params is not None)
        except Exception:
            log(f"[{day}] UNCAUGHT, keeping last-known-good:\n{traceback.format_exc()}")
    with open(MARKER, "w") as fh:                # assert on artifacts, not exit codes
        fh.write(datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
