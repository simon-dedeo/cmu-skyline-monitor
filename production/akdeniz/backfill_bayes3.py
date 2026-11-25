#!/usr/bin/env python3
"""backfill_bayes3.py -- full 4-month archive reprocessing on akdeniz (32 cores).

Stage 1 (--build-tmaps):
  Builds biweekly CRF-calibrated, velocity-registered multi-bracket transmittance maps
  (TOP_MASK=20, ridge-regularized sky ring) across the full Jul-Nov 2026 trajectory so each
  era reflects its exact local off-axis sec(theta) projection and top-edge geometry.

Stage 2 (--run):
  Reprocesses every matched raw daylight bracket triple in parallel using the exact
  bayes3-subpx algorithm (empirical CRF + Stan M2 scatter prior + conjugate MAP pooling +
  symmetric subpixel parabolic lock + 2-iter Gauss-Newton rim alignment + bicubic warp +
  np.rint rounding), fuses with Mertens + hdrfuse.enhance (JPEG Q=92), safety-gates
  clean frames against worsening, writes staged JPEGs to ~/archive_reprocess/out/, and
  emits a monthly audit table + visual verification crops.
"""
import argparse
import collections
import csv
import datetime
import glob
import json
import os
import statistics as st
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Pool

import cv2
import numpy as np

cv2.setNumThreads(1)

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "archive_reprocess")
sys.path.insert(0, BASE)
import flatfield
import hdrfuse

BRACKETS = os.path.join(HOME, "brackets")
STATE_DIR = os.path.join(HOME, "spot_state")
TMAP_DIR = os.path.join(BASE, "tmaps")
OUT_DIR = os.path.join(BASE, "out")
SAMPLE_DIR = os.path.join(BASE, "samples")
LISTING = os.path.join(BASE, "archive_listing.txt")
AUDIT_CSV = os.path.join(BASE, "audit.csv")

FULL_W, FULL_H = 3840, 2160
HALF = 150
TOP_MASK = 20
RING0, RING1 = 85, 115
R_CORE, R_DISK = 25, 60
RIDGE_QUAD = (50.0, 50.0, 200.0)


# ------------------------------------------------------------------ Trajectory
def load_trajectory():
    """Return sorted [(unix_ts, cx, cy), ...] from Jul 20 anchor + history_v1 + history."""
    pts = []
    # Anchor on Jul 20 from photometry.csv smoketest (1163, 187)
    t_init = datetime.datetime(2026, 7, 20, 18, 0, tzinfo=datetime.timezone.utc).timestamp()
    pts.append((t_init, 1163.0, 187.0))

    h1 = os.path.join(STATE_DIR, "history_v1.csv")
    if os.path.exists(h1):
        with open(h1) as fh:
            for r in csv.DictReader(fh):
                if r.get("accepted") == "1":
                    d = datetime.datetime.strptime(r["day"], "%Y%m%d").replace(tzinfo=datetime.timezone.utc)
                    # Mean epoch of v1 trailing-day run is ~18:00 UTC of preceding day
                    ts = (d - datetime.timedelta(hours=6)).timestamp()
                    pts.append((ts, float(r["cx"]), float(r["cy"])))

    h2 = os.path.join(STATE_DIR, "history.csv")
    if os.path.exists(h2):
        with open(h2) as fh:
            for r in csv.DictReader(fh):
                if r.get("status") == "ok" and r.get("epoch"):
                    ts = datetime.datetime.fromisoformat(r["epoch"]).timestamp()
                    pts.append((ts, float(r["cx"]), float(r["cy"])))

    # Deduplicate by timestamp (keep latest) and sort
    by_ts = {}
    for ts, x, y in pts:
        by_ts[round(ts)] = (ts, x, y)
    out = [by_ts[k] for k in sorted(by_ts)]
    return np.array([p[0] for p in out]), np.array([p[1] for p in out]), np.array([p[2] for p in out])


def interp_pos(ts, traj):
    t_arr, x_arr, y_arr = traj
    return float(np.interp(ts, t_arr, x_arr)), float(np.interp(ts, t_arr, y_arr))


def stem_ts(stem):
    return datetime.datetime.strptime(stem[:15], "%Y%m%d_%H%M%S").replace(
        tzinfo=datetime.timezone.utc).timestamp()


# ------------------------------------------------------------------ Stage 1: Biweekly CRF TMAPs
def _sky_design(h2, bx, by):
    yy, xx = np.mgrid[0:h2, 0:h2]
    rb = np.hypot(xx - bx, yy - by)
    ring = (rb >= RING0) & (rb < RING1) & (yy >= TOP_MASK)
    if ring.sum() < 400:
        ring = (rb >= 70) & (rb < 130) & (yy >= TOP_MASK)
    ay, ax = np.where(ring)
    ur, vr = (ax - bx) / 100.0, (ay - by) / 100.0
    ue, ve = ((xx - bx) / 100.0).ravel(), ((yy - by) / 100.0).ravel()
    A = np.column_stack([np.ones_like(ur), ur, vr, ur * ur, ur * vr, vr * vr])
    E = np.column_stack([np.ones_like(ue), ue, ve, ue * ue, ue * ve, ve * ve])
    pen = RIDGE_QUAD if by < RING1 + TOP_MASK else (2.0, 2.0, 2.0)
    R = np.diag([0.0, 0.0, 0.0, pen[0], pen[1], pen[2]])
    return yy, xx, rb, ring, A, E, R


def build_one_era(era_idx, d0_str, d1_str, all_files, traj, live_mm):
    t0 = datetime.datetime.strptime(d0_str, "%Y%m%d").replace(tzinfo=datetime.timezone.utc).timestamp()
    t1 = datetime.datetime.strptime(d1_str, "%Y%m%d").replace(tzinfo=datetime.timezone.utc).timestamp()
    t_mid = 0.5 * (t0 + t1)
    cx_e, cy_e = interp_pos(t_mid, traj)

    x0 = max(0, min(FULL_W - 2 * HALF, int(round(cx_e)) - HALF))
    y0 = max(0, min(FULL_H - 2 * HALF, int(round(cy_e)) - HALF))
    h2 = 2 * HALF
    bx, by = cx_e - x0, cy_e - y0
    yy, xx, rb, ring, A, E, R = _sky_design(h2, bx, by)
    crf = live_mm["crf"]

    era_files = [p for p in all_files if d0_str <= os.path.basename(p)[:8] < d1_str]
    if len(era_files) > 500:
        idx = np.linspace(0, len(era_files) - 1, 500, dtype=int)
        era_files = [era_files[i] for i in idx]

    def _proc(p):
        im = cv2.imread(p)
        if im is None or im.shape[:2] != (FULL_H, FULL_W):
            return None
        ts = stem_ts(os.path.basename(p))
        cx_f, cy_f = interp_pos(ts, traj)
        # Extract patch centered at (x0, y0) and shift frame so (cx_f, cy_f) lands on (cx_e, cy_e)
        sub = im[y0:y0 + h2, x0:x0 + h2].astype(np.float64)
        dx, dy = cx_e - cx_f, cy_e - cy_f
        if abs(dx) > 45 or abs(dy) > 45:
            return None
        if abs(dx) > 0.1 or abs(dy) > 0.1:
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            sub = np.stack([cv2.warpAffine(sub[:, :, c], M, (h2, h2), flags=cv2.INTER_LINEAR,
                                           borderMode=cv2.BORDER_REPLICATE) for c in range(3)], axis=2)
        out = [None, None, None]
        for c, ch in enumerate("bgr"):
            raw = sub[:, :, c]
            med = float(np.median(raw[ring]))
            if med < 35 or med > 245 or (raw[ring] > 249).mean() > 0.005 or (raw[ring] < 5).mean() > 0.005:
                continue
            # Encoded-DN sky flatness gate
            _, mad_dn = flatfield._fit_sky(raw[ring], A, E, R, (h2, h2))
            if mad_dn > 2.5:
                continue
            a, g_exp = crf[ch]
            lin = flatfield._crf_eval(raw, a, g_exp)
            bg, _ = flatfield._fit_sky(lin[ring], A, E, R, (h2, h2))
            if float(np.median(bg[ring])) < 0.02:
                continue
            rat = np.clip(lin / np.maximum(bg, 1e-6), 0.75, 1.15).astype(np.float32)
            out[c] = rat
        return out if any(x is not None for x in out) else None

    ch_stacks = [[], [], []]
    with ThreadPoolExecutor(max_workers=16) as ex:
        for res in ex.map(_proc, era_files):
            if res is None:
                continue
            for c in range(3):
                if res[c] is not None:
                    ch_stacks[c].append(res[c])

    n_ch = [len(s) for s in ch_stacks]
    if min(n_ch) < 15:
        print(f"  WARN era {era_idx} ({d0_str}-{d1_str}): low frames {n_ch}, falling back to live TMAP shape")
        T = np.load(os.path.join(STATE_DIR, "spot_tmap_live.npy")).astype(np.float32)
        lm = live_mm["tmap"]
        T = flatfield._shift(T, bx - float(lm["mx"]), by - float(lm["my"]))
    else:
        T = np.ones((h2, h2, 3), dtype=np.float32)
        for c in range(3):
            med_t = np.median(np.stack(ch_stacks[c], axis=0), axis=0).astype(np.float32)
            T[:, :, c] = cv2.GaussianBlur(med_t, (0, 0), 1.5)

    # Measure empirical centre mx, my and core_depth / core_scale
    core = (rb < R_CORE) & (yy >= TOP_MASK)
    disk = (rb < R_DISK) & (yy >= TOP_MASK)
    dip_g = np.clip(1.0 - T[:, :, 1], 0.0, 0.25)
    sm = cv2.GaussianBlur(dip_g, (0, 0), 4)
    near = (rb < 65) & (yy >= TOP_MASK)
    pk = float(np.percentile(sm[near], 99))
    hm = near & (sm >= 0.5 * pk)
    mx = float(np.mean(xx[hm])) if hm.sum() > 200 else bx
    my = float(np.mean(yy[hm])) if hm.sum() > 200 else by

    core_depth, core_scale = {}, {}
    for c, ch in enumerate("bgr"):
        cd = float(np.median(1.0 - T[:, :, c][core]))
        dd = max(float(np.median(1.0 - T[:, :, c][disk])), 1e-4)
        core_depth[ch] = round(cd, 5)
        core_scale[ch] = round(float(np.clip(cd / dd, 1.0, 1.5)), 3)

    npy_path = os.path.join(TMAP_DIR, f"era_{era_idx:02d}.npy")
    json_path = os.path.join(TMAP_DIR, f"era_{era_idx:02d}.json")
    np.save(npy_path, T.astype(np.float32))
    bundle = {
        "schema": 3,
        "era_idx": era_idx,
        "d0": d0_str,
        "d1": d1_str,
        "t0": t0,
        "t1": t1,
        "tmap": {
            "x0": x0, "y0": y0, "half": HALF,
            "bx": round(bx, 2), "by": round(by, 2),
            "mx": round(mx, 2), "my": round(my, 2),
            "n_ch": n_ch,
            "core_depth": core_depth,
            "core_scale": core_scale,
        },
        "crf": crf,
        "bayes": live_mm["bayes"],
    }
    with open(json_path, "w") as fh:
        json.dump(bundle, fh, indent=2)
    print(f"  era {era_idx:02d} [{d0_str}..{d1_str}) pos=({cx_e:.1f},{cy_e:.1f}) n_ch={n_ch} "
          f"core_depth=B:{core_depth['b']*100:.2f}% G:{core_depth['g']*100:.2f}% R:{core_depth['r']*100:.2f}%")
    return bundle


def build_all_tmaps():
    os.makedirs(TMAP_DIR, exist_ok=True)
    traj = load_trajectory()
    live_mm = json.load(open(os.path.join(STATE_DIR, "spot_model_live.json")))
    all_files = sorted(glob.glob(os.path.join(BRACKETS, "*_br*.png")))
    print(f"Building biweekly CRF TMAPs from {len(all_files)} raw brackets...")

    # 9 eras covering 2026-07-20 through 2026-11-26 (~14 days each)
    d_start = datetime.date(2026, 7, 20)
    d_end = datetime.date(2026, 11, 26)
    eras = []
    cur = d_start
    idx = 0
    while cur < d_end:
        nxt = min(cur + datetime.timedelta(days=14), d_end)
        if (d_end - nxt).days < 6:
            nxt = d_end
        eras.append((idx, cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d")))
        cur = nxt
        idx += 1

    bundles = []
    for era_idx, d0_str, d1_str in eras:
        bundles.append(build_one_era(era_idx, d0_str, d1_str, all_files, traj, live_mm))
    with open(os.path.join(TMAP_DIR, "index.json"), "w") as fh:
        json.dump(bundles, fh, indent=2)
    print(f"Built {len(bundles)} biweekly CRF TMAPs -> {TMAP_DIR}")


# ------------------------------------------------------------------ Stage 2: Parallel Reprocess
_G = {}


def _init_worker(traj, era_list):
    cv2.setNumThreads(1)
    _G["traj"] = traj
    _G["eras"] = []
    for b in era_list:
        T = np.load(os.path.join(TMAP_DIR, f"era_{b['era_idx']:02d}.npy")).astype(np.float32)
        _G["eras"].append((b, T))
    _G["merge"] = cv2.createMergeMertens()


def _srgb_lin(v):
    v = np.asarray(v, np.float64) / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _dip_metrics(img, cx, cy):
    H, W = img.shape[:2]
    y0, y1 = max(0, int(cy - RING1 - 10)), min(H, int(cy + RING1 + 10))
    x0, x1 = max(0, int(cx - RING1 - 10)), min(W, int(cx + RING1 + 10))
    win = img[y0:y1, x0:x1].astype(np.float64)
    if win.size == 0:
        return None, None, None
    rx, ry = cx - x0, cy - y0
    yy, xx = np.mgrid[0:win.shape[0], 0:win.shape[1]]
    rb = np.hypot(xx - rx, yy - ry)
    ring = (rb >= RING0) & (rb < RING1) & (yy + y0 >= TOP_MASK)
    core = (rb < R_CORE) & (yy + y0 >= TOP_MASK)
    if ring.sum() < 400 or core.sum() < 200:
        return None, None, None
    ay, ax = np.where(ring)
    cyy, cxx = np.where(core)
    ur, vr = (ax - rx) / 100.0, (ay - ry) / 100.0
    uc, vc = (cxx - rx) / 100.0, (cyy - ry) / 100.0
    A = np.column_stack([np.ones_like(ur), ur, vr, ur * ur, ur * vr, vr * vr])
    Ac = np.column_stack([np.ones_like(uc), uc, vc, uc * uc, uc * vc, vc * vc])
    pen = RIDGE_QUAD if cy < RING1 + TOP_MASK else (2.0, 2.0, 2.0)
    R = np.diag([0.0, 0.0, 0.0, pen[0], pen[1], pen[2]])

    def _one(lin):
        vals = lin[ring]
        coef = np.linalg.solve(A.T @ A + R, A.T @ vals)
        res = vals - A @ coef
        mad = float(np.median(np.abs(res - np.median(res)))) * 1.4826 + 1e-9
        keep = np.abs(res) < 3.0 * mad
        if keep.sum() > 200:
            Ak = A[keep]
            coef = np.linalg.solve(Ak.T @ Ak + R, Ak.T @ vals[keep])
            res = vals[keep] - Ak @ coef
            mad = float(np.median(np.abs(res - np.median(res)))) * 1.4826 + 1e-9
        bg = float(np.median(Ac @ coef))
        bg_r = float(np.median(A @ coef))
        if bg <= 1e-6 or bg_r <= 1e-6:
            return None, None
        return 100.0 * (1.0 - float(np.median(lin[core])) / bg), mad / bg_r

    gd, rough = _one(_srgb_lin(win.mean(axis=2)))
    if gd is None:
        return None, None, None
    cdips = []
    for c in range(3):
        d, _ = _one(_srgb_lin(win[:, :, c]))
        cdips.append(0.0 if d is None else d)
    return gd, cdips, rough


def _pick_era(ts):
    best = _G["eras"][-1]
    for b, T in _G["eras"]:
        if b["t0"] <= ts < b["t1"]:
            return b, T
    return best


def _reprocess_one(job):
    stem, arch_rel, raw_blobs = job
    imgs = []
    for buf in raw_blobs:
        arr = np.frombuffer(buf, dtype=np.uint8)
        im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if im is not None:
            imgs.append(im)
    if len(imgs) < 2:
        return None
    shape = imgs[0].shape
    imgs = [im for im in imgs if im.shape == shape]
    if len(imgs) < 2 or shape[:2] != (FULL_H, FULL_W):
        return None

    # Quick night skip: if brightest bracket median < 14 DN, spot is invisible in dark sky
    max_med = max(float(np.median(im[::8, ::8])) for im in imgs)
    if max_med < 14.0:
        return (stem, arch_rel, -1, round(max_med, 1), 0, 0.0, "", "", "", "", "", "", "night_skip")

    ts = stem_ts(stem)
    px, py = interp_pos(ts, _G["traj"])
    mm, T_base = _pick_era(ts)
    tm = mm["tmap"]
    x0, y0, h = int(tm["x0"]), int(tm["y0"]), int(tm["half"])
    rx0, ry0 = float(tm["mx"]), float(tm["my"])

    # Prepare float copies & window views
    out = [im.astype(np.float32) for im in imgs]
    wins = [o[y0:y0 + 2 * h, x0:x0 + 2 * h] for o in out]

    # Step 1: initial shift to smoothed trajectory position
    sx, sy = px - (x0 + rx0), py - (y0 + ry0)
    mag = float(np.hypot(sx, sy))
    if mag > 45.0:
        sx, sy = sx * 45.0 / mag, sy * 45.0 / mag
    T = flatfield._shift(T_base, sx, sy) if (abs(sx) > 0.05 or abs(sy) > 0.05) else T_base
    rx, ry = rx0 + sx, ry0 + sy

    # Step 2: symmetric subpixel template lock + 2-iter Gauss-Newton rim refinement
    lock, pk = 0, 0.0
    try:
        dx, dy, pk_v, pk2 = flatfield.measure_lock(wins[flatfield._best_bracket(wins)], T, rx, ry)
        if pk_v is not None:
            pk = float(pk_v)
        if dx is not None and np.hypot(dx, dy) <= 22.0:
            lock = 1
            sx, sy = sx + dx, sy + dy
            T = flatfield._shift(T_base, sx, sy)
            rx, ry = rx0 + sx, ry0 + sy
            if pk >= 0.65:
                for _ in range(2):
                    ddx, ddy = flatfield._refine_rim(wins, T, rx, ry, mm)
                    if abs(ddx) <= 0.05 and abs(ddy) <= 0.05:
                        break
                    sx, sy = sx + ddx, sy + ddy
                    T = flatfield._shift(T_base, sx, sy)
                    rx, ry = rx0 + sx, ry0 + sy
    except Exception:
        pass

    cx_fin, cy_fin = x0 + rx, y0 + ry
    flatfield._flatten_set(out, wins, T, rx, ry, mm)
    cimgs = [np.rint(np.clip(o, 0, 255)).astype(np.uint8) for o in out]

    merge = _G["merge"]
    # Only fuse m_bef on a local crop around (cx_fin, cy_fin) for the before-dip metric (25x faster than full 4K!)
    cy0 = max(0, int(cy_fin - RING1 - 24)); cy1 = min(FULL_H, int(cy_fin + RING1 + 24))
    cx0 = max(0, int(cx_fin - RING1 - 24)); cx1 = min(FULL_W, int(cx_fin + RING1 + 24))
    m_bef_crop = np.clip(merge.process([im[cy0:cy1, cx0:cx1] for im in imgs]) * 255.0, 0, 255).astype(np.uint8)
    m_aft = np.clip(merge.process(cimgs) * 255.0, 0, 255).astype(np.uint8)
    lvl = float(np.median(m_aft[::16, ::16]))

    mb, _, rough = _dip_metrics(m_bef_crop, cx_fin - cx0, cy_fin - cy0)
    ma, ca, _ = _dip_metrics(m_aft[cy0:cy1, cx0:cx1], cx_fin - cx0, cy_fin - cy0)

    # Safety gate: never degrade a clean-sky frame!
    if mb is not None and ma is not None and rough is not None:
        if rough <= 0.045 and lvl >= 45 and abs(ma) > abs(mb) + 0.35:
            return (stem, arch_rel, mm["era_idx"], round(lvl, 1), lock, round(pk, 3),
                    round(mb, 3), round(ma, 3), round(ca[0], 3), round(ca[1], 3), round(ca[2], 3),
                    round(rough, 4), "gate_skip")

    e_aft = hdrfuse.enhance(m_aft)
    out_path = os.path.join(OUT_DIR, arch_rel)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv2.imwrite(out_path, e_aft, [cv2.IMWRITE_JPEG_QUALITY, hdrfuse.JPEG_Q])

    if mb is None or ma is None:
        return (stem, arch_rel, mm["era_idx"], round(lvl, 1), lock, round(pk, 3),
                "", "", "", "", "", "", "written")
    return (stem, arch_rel, mm["era_idx"], round(lvl, 1), lock, round(pk, 3),
            round(mb, 3), round(ma, 3), round(ca[0], 3), round(ca[1], 3), round(ca[2], 3),
            round(rough, 4), "written")


def load_archive_index():
    arch = collections.defaultdict(list)
    with open(LISTING) as fh:
        for line in fh:
            s = line.strip()
            if not s or "/" not in s:
                continue
            day, name = s.split("/", 1)
            hhmm = name[:4]
            if hhmm.isdigit():
                arch[day].append((int(hhmm[:2]) * 60 + int(hhmm[2:]), s))
    for d in arch:
        arch[d].sort()
    return arch


def match_archive(stem, arch):
    day = f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}"
    lst = arch.get(day)
    if not lst:
        return None
    mins = int(stem[9:11]) * 60 + int(stem[11:13])
    best = min(lst, key=lambda x: abs(x[0] - mins))
    if abs(best[0] - mins) > 3:
        return None
    return best[1]


def make_visual_samples(rows, groups, traj, eras):
    """Save 6 monthly side-by-side BEFORE | AFTER | 15x DIFF panels for visual inspection."""
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    buckets = {
        "2026-07": [], "2026-08": [], "2026-09": [],
        "2026-10": [], "2026-11-early": [], "2026-11-late": [],
    }
    for r in rows:
        if r[12] != "written" or r[6] == "" or r[7] == "":
            continue
        lvl, lock, mb, ma, rough = r[3], r[4], r[6], r[7], r[11]
        if lvl < 90 or lock != 1 or rough > 0.025 or mb < 2.8:
            continue
        ym = r[1][:7]
        if ym == "2026-11":
            key = "2026-11-early" if int(r[1][8:10]) <= 14 else "2026-11-late"
        elif ym in buckets:
            key = ym
        else:
            continue
        buckets[key].append(r)

    merge = cv2.createMergeMertens()
    for key, cand in buckets.items():
        if not cand:
            continue
        # Pick the median-cleanest midday frame (lowest roughness)
        cand.sort(key=lambda r: (r[11], abs(r[7])))
        pick = cand[0]
        stem, arch_rel = pick[0], pick[1]
        imgs = [cv2.imread(p) for p in groups[stem]]
        ts = stem_ts(stem)
        cx, cy = interp_pos(ts, traj)
        m_bef = np.clip(merge.process(imgs) * 255.0, 0, 255).astype(np.uint8)
        e_bef = hdrfuse.enhance(m_bef)
        e_aft = cv2.imread(os.path.join(OUT_DIR, arch_rel))
        y0 = max(0, int(cy) - 140)
        y1 = min(FULL_H, y0 + 280)
        x0 = max(0, int(cx) - 180)
        x1 = min(FULL_W, x0 + 360)
        cb = e_bef[y0:y1, x0:x1]
        ca = e_aft[y0:y1, x0:x1]
        df = np.clip(128.0 + 6.0 * (ca.astype(np.float64).mean(2) - cb.astype(np.float64).mean(2)),
                     0, 255).astype(np.uint8)
        df3 = cv2.cvtColor(df, cv2.COLOR_GRAY2BGR)
        panel = np.hstack([cb, ca, df3])
        txt = f"{key} ({stem})  Mertens {pick[6]:+.2f}%->{pick[7]:+.2f}%  BGR[{pick[8]:+.1f},{pick[9]:+.1f},{pick[10]:+.1f}]"
        cv2.putText(panel, txt, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(panel, txt, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        out_p = os.path.join(SAMPLE_DIR, f"verify_{key}.jpg")
        cv2.imwrite(out_p, panel, [cv2.IMWRITE_JPEG_QUALITY, 94])
        print(f"  sample {key:14s}: {stem} -> {out_p} (Mertens {pick[6]:+.2f}%->{pick[7]:+.2f}%)")


def run_reprocess(workers=28, limit=0):
    t_start = time.time()
    traj = load_trajectory()
    eras = json.load(open(os.path.join(TMAP_DIR, "index.json")))
    arch = load_archive_index()

    groups = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(BRACKETS, "*_br*.png"))):
        groups[os.path.basename(p)[:15]].append(p)

    # Match to archive (if two bracket stems map to same archive JPEG, keep latest)
    by_arch = {}
    for stem, paths in sorted(groups.items()):
        if len(paths) < 2:
            continue
        rel = match_archive(stem, arch)
        if rel:
            by_arch[rel] = (stem, rel, sorted(paths))
    jobs = [by_arch[k] for k in sorted(by_arch)]
    if limit > 0:
        jobs = jobs[:limit]
    print(f"Matched {len(jobs)} raw bracket triples to pandr archive JPEGs; sequential HDD reader -> {workers} compute cores...")

    def _seq_reader(job_list):
        """Read raw PNG bytes sequentially from /dev/sdb in a single thread (eliminates HDD seek thrashing)."""
        for stem, rel, paths in job_list:
            blobs = []
            for p in paths:
                try:
                    with open(p, "rb") as fh:
                        blobs.append(fh.read())
                except OSError:
                    pass
            if len(blobs) >= 2:
                yield (stem, rel, blobs)

    rows = []
    n_done = 0
    # Keep bounded queue via apply_async/deque so RAM stays < 1 GB while HDD streams sequentially
    with Pool(processes=workers, initializer=_init_worker, initargs=(traj, eras)) as pool:
        pending = collections.deque()
        max_inflight = max(workers * 2, 32)
        for item in _seq_reader(jobs):
            pending.append(pool.apply_async(_reprocess_one, (item,)))
            if len(pending) >= max_inflight:
                res = pending.popleft().get()
                n_done += 1
                if res is not None:
                    rows.append(res)
                if n_done % 500 == 0:
                    el = time.time() - t_start
                    rate = n_done / max(el, 1e-3)
                    eta = (len(jobs) - n_done) / max(rate, 1e-3)
                    print(f"  [{n_done:5d}/{len(jobs)}] {el:5.0f}s elapsed ({rate:.1f} triples/s, ETA {eta:.0f}s)", flush=True)
        while pending:
            res = pending.popleft().get()
            n_done += 1
            if res is not None:
                rows.append(res)
            if n_done % 500 == 0 or n_done == len(jobs):
                el = time.time() - t_start
                rate = n_done / max(el, 1e-3)
                eta = (len(jobs) - n_done) / max(rate, 1e-3)
                print(f"  [{n_done:5d}/{len(jobs)}] {el:5.0f}s elapsed ({rate:.1f} triples/s, ETA {eta:.0f}s)", flush=True)

    rows.sort(key=lambda r: r[0])
    hdr = ["stem", "arch_rel", "era", "lvl", "lock", "pk", "m_before", "m_after",
           "after_b", "after_g", "after_r", "rough", "status"]
    with open(AUDIT_CSV, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        w.writerows(rows)

    # Print monthly summary
    by_status = collections.Counter(r[12] for r in rows)
    print(f"\nDONE in {time.time() - t_start:.1f}s. Counts: {dict(by_status)}")
    by_mo = collections.defaultdict(list)
    for r in rows:
        if r[12] == "written" and r[6] != "" and r[11] <= 0.04 and r[3] >= 50:
            by_mo[r[1][:7]].append(r)
    print("\n=== CLEAN DAYLIGHT MERTENS LINEAR AUDIT BY MONTH (rough <= 0.04, lvl >= 50) ===")
    all_cl = []
    for mo in sorted(by_mo):
        cl = by_mo[mo]
        all_cl.extend(cl)
        mb = [r[6] for r in cl]; ma = [r[7] for r in cl]
        ab = [r[8] for r in cl]; ag = [r[9] for r in cl]; ar = [r[10] for r in cl]
        worse = sum(1 for x, y in zip(mb, ma) if abs(y) > abs(x))
        print(f"  {mo}: n={len(cl):4d}  before={st.median(mb):+5.2f}% -> after={st.median(ma):+5.2f}% "
              f"(|after|={st.median([abs(y) for y in ma]):.2f}%)  "
              f"BGR=[{st.median(ab):+.2f}%, {st.median(ag):+.2f}%, {st.median(ar):+.2f}%]  "
              f"worse={worse}/{len(cl)} ({100*worse/len(cl):.1f}%)")
    if all_cl:
        mb = [r[6] for r in all_cl]; ma = [r[7] for r in all_cl]
        ab = [r[8] for r in all_cl]; ag = [r[9] for r in all_cl]; ar = [r[10] for r in all_cl]
        worse = sum(1 for x, y in zip(mb, ma) if abs(y) > abs(x))
        print(f"  TOTAL  : n={len(all_cl):4d}  before={st.median(mb):+5.2f}% -> after={st.median(ma):+5.2f}% "
              f"(|after|={st.median([abs(y) for y in ma]):.2f}%)  "
              f"BGR=[{st.median(ab):+.2f}%, {st.median(ag):+.2f}%, {st.median(ar):+.2f}%]  "
              f"worse={worse}/{len(all_cl)} ({100*worse/len(all_cl):.1f}%)")

    print("\nGenerating monthly visual verification panels...")
    make_visual_samples(rows, groups, traj, eras)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-tmaps", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--workers", type=int, default=28)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if args.build_tmaps:
        build_all_tmaps()
    if args.run:
        run_reprocess(workers=args.workers, limit=args.limit)


if __name__ == "__main__":
    main()
