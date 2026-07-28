"""backfill.py -- re-fuse every complete bracket triple with the per-day pinned maps and
closed-loop display-space gain; write corrected JPEGs named to match the served archive.

Derived from sweep2.py (validated: 88% median removal, 0-1 inversions over 1375 ticks).
Differences: writes output frames; maps bracket stems to archive filenames (the archive
HHMM is stamped at tick START, the bracket harvest ~20-40s later, so match same UTC day
within +/-3 min, nearest); skips nights, unmapped ticks, and any tick the correction
would WORSEN (|after| > |before| + 0.3 with a real dip) -- originals are kept for those.
"""
import os, glob, sys, json, csv, numpy as np, cv2
from multiprocessing import Pool
sys.path.insert(0, os.path.expanduser("~"))
import hdrfuse

# one OpenCV thread per pool worker -- oversubscription made 30 workers run 5x slow
cv2.setNumThreads(1)

BR = os.path.expanduser("~/brackets")
MAPS = os.path.expanduser("~/spotmaps")
OUT = os.path.expanduser("~/corrected")
LISTING = os.path.expanduser("~/archive_listing.txt")
R_FP, R0, R1, TOP_MASK = 90, 110, 170, 20
G1, G2 = 1.0, 2.2
GCLAMP = (0.2, 6.0)
JPEG_Q = 92
os.makedirs(OUT, exist_ok=True)
GEOM = json.load(open(f"{MAPS}/geom.json"))
MAPMETA = {}
for r in csv.DictReader(open(f"{MAPS}/maps.csv")):
    MAPMETA[(r["day"], r["bucket"], r["ch"])] = r

# archive filename map: (day, minute-of-day) -> HHMM string
ARCH = {}
for line in open(LISTING):
    line = line.strip()
    if not line:
        continue
    day, name = line.split("/")
    hhmm = name[:4]
    ARCH.setdefault(day, []).append(int(hhmm[:2]) * 60 + int(hhmm[2:]))
for d in ARCH:
    ARCH[d].sort()


def archive_name(stem):
    """bracket stem 'YYYYMMDD_HHMMSS' -> (day-dir, HHMM) of the served frame, or None."""
    day = f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}"
    if day not in ARCH:
        return None
    mins = int(stem[9:11]) * 60 + int(stem[11:13])
    best = min(ARCH[day], key=lambda m: abs(m - mins))
    if abs(best - mins) > 3:
        return None
    return day, f"{best//60:02d}{best%60:02d}"


def load3(p):
    img = cv2.imread(p, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    return img[:, :, :3] if (img.ndim == 3 and img.shape[2] > 3) else img


HALF = 230


def ring_coef(ch, cx, cy):
    H, W = ch.shape
    y0 = max(TOP_MASK, int(cy) - HALF); y1 = min(H, int(cy) + HALF)
    x0 = max(0, int(cx) - HALF); x1 = min(W, int(cx) + HALF)
    sub = ch[y0:y1, x0:x1]
    gy, gx = np.mgrid[y0:y1, x0:x1]
    rr = np.hypot(gx - cx, gy - cy)
    m = (rr >= R0) & (rr < R1) & (gy >= TOP_MASK)
    if m.sum() < 200:
        return None
    un, vn = (gx[m] - cx) / 100.0, (gy[m] - cy) / 100.0
    A = np.column_stack([np.ones(un.size), un, vn, un**2, un*vn, vn**2])
    vals = sub[m].astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
    res = vals - A @ coef
    mad = np.median(np.abs(res - np.median(res))) + 1e-9
    keep = np.abs(res) < 4 * 1.4826 * mad
    if keep.sum() > 150:
        coef, *_ = np.linalg.lstsq(A[keep], vals[keep], rcond=None)
    return coef


def bg_full(ch, cx, cy):
    coef = ring_coef(ch, cx, cy)
    if coef is None:
        return None
    H, W = ch.shape
    y0 = max(TOP_MASK, int(cy) - HALF); y1 = min(H, int(cy) + HALF)
    x0 = max(0, int(cx) - HALF); x1 = min(W, int(cx) + HALF)
    gy, gx = np.mgrid[y0:y1, x0:x1]
    un, vn = (gx - cx) / 100.0, (gy - cy) / 100.0
    bg = (coef[0] + coef[1]*un + coef[2]*vn + coef[3]*un**2 + coef[4]*un*vn + coef[5]*vn**2)
    out = ch.astype(np.float64).copy()
    out[y0:y1, x0:x1] = bg
    return out


def fuse(imgs):
    f = cv2.createMergeMertens().process([np.ascontiguousarray(i) for i in imgs])
    return hdrfuse.enhance(np.clip(f * 255.0, 0, 255).astype(np.uint8))


def _work(item):
    stem, bs = item
    day8 = stem[:8]
    an = archive_name(stem)
    if day8 not in GEOM["pinned"] or an is None:
        return dict(stem=stem, note="unmapped" if an is None else "no-maps")
    cx, cy = (float(v) for v in GEOM["pinned"][day8])
    imgs = [load3(bs[bk]) for bk in ("br0", "br1", "br2")]
    if any(i is None for i in imgs):
        return dict(stem=stem, note="unreadable")
    H, W = imgs[0].shape[:2]

    # --- PER-TICK TEMPLATE LOCK (2026-07-28): the blemish migrates 7-18 px WITHIN a day,
    #     so one centre per day leaves a bright/dark dipole on the day's extremes. Measure
    #     this tick's own dip against the day map template and shift the correction onto
    #     it; gated (correlation >= 0.40, |shift| <= 25 px), else the day centre stands.
    try:
        mb = MAPMETA.get((day8, "br1", "g")) or next(
            (MAPMETA[k] for k in MAPMETA if k[0] == day8), None)
        if mb is not None:
            x_lo, y_lo = int(mb["x_lo"]), int(mb["y_lo"])
            tw = np.mean([np.load(f"{MAPS}/{day8}_br1_{c}.npy").astype(np.float32)
                          for c in "bgr" if (day8, "br1", c) in MAPMETA], axis=0)
            tpl0 = cv2.GaussianBlur((1.0 - tw), (0, 0), 4)
            g1 = imgs[1].astype(np.float32).mean(axis=2)
            sub = g1[y_lo:y_lo + tw.shape[0], x_lo:x_lo + tw.shape[1]]
            if sub.shape == tw.shape and float(np.median(sub)) > 40:
                bgs2 = cv2.GaussianBlur(sub, (0, 0), 45)
                dmap = cv2.GaussianBlur(np.clip(1.0 - sub / np.maximum(bgs2, 1e-3), -0.1, 0.3),
                                        (0, 0), 4)
                lx0, ly0 = cx - x_lo, cy - y_lo
                ty0, ty1 = int(max(ly0 - 60, 0)), int(min(ly0 + 60, tw.shape[0]))
                tx0, tx1 = int(max(lx0 - 60, 0)), int(min(lx0 + 60, tw.shape[1]))
                tpl = tpl0[ty0:ty1, tx0:tx1]
                sy0, sy1 = max(ty0 - 25, 0), min(ty1 + 25, tw.shape[0])
                sx0, sx1 = max(tx0 - 25, 0), min(tx1 + 25, tw.shape[1])
                cc = cv2.matchTemplate(dmap[sy0:sy1, sx0:sx1], tpl, cv2.TM_CCOEFF_NORMED)
                _, pk, _, loc = cv2.minMaxLoc(cc)
                if pk >= 0.40:
                    lkx = (sx0 + loc[0]) - tx0
                    lky = (sy0 + loc[1]) - ty0
                    if abs(lkx) <= 25 and abs(lky) <= 25:
                        cx, cy = cx + lkx, cy + lky
    except Exception:
        pass
    gy, gx = np.mgrid[0:H, 0:W]
    rr = np.hypot(gx - cx, gy - cy)
    feather = np.clip((100.0 - rr) / 30.0, 0, 1)
    disk = rr < 45

    shapes, bgs = {}, {}
    for bi, bk in enumerate(("br0", "br1", "br2")):
        for ci, chn in enumerate("bgr"):
            meta = MAPMETA.get((day8, bk, chn))
            if meta is None:
                continue
            peak = float(meta["peak_pct"]) / 100.0
            if peak <= 0.001:
                continue
            T = np.load(f"{MAPS}/{day8}_{bk}_{chn}.npy").astype(np.float64)
            px0, py0 = (float(v) for v in GEOM["pinned"][day8])
            x_lo = int(meta["x_lo"]) + int(round(cx - px0))
            y_lo = int(meta["y_lo"]) + int(round(cy - py0))
            hh, ww = T.shape
            y1, x1 = min(H, y_lo + hh), min(W, x_lo + ww)
            y_lo2, x_lo2 = max(0, y_lo), max(0, x_lo)
            S = np.zeros((H, W))
            S[y_lo2:y1, x_lo2:x1] = np.clip(
                (1.0 - T[y_lo2-y_lo:y1-y_lo, x_lo2-x_lo:x1-x_lo]) / peak, 0, 1)
            shapes[(bi, ci)] = (cv2.GaussianBlur(S.astype(np.float32), (0, 0), 2).astype(np.float64), peak)
            bg = bg_full(imgs[bi][:, :, ci].astype(np.float64), cx, cy)
            if bg is not None:
                bgs[(bi, ci)] = bg
    if len(shapes) < 4:
        return dict(stem=stem, note="few-maps")

    def corrected(g):
        out = []
        for bi in range(3):
            o = imgs[bi].astype(np.float32).copy()
            for ci in range(3):
                key = (bi, ci)
                if key not in shapes or key not in bgs:
                    continue
                S, peak = shapes[key]
                ch = imgs[bi][:, :, ci].astype(np.float64)
                T = np.clip(1.0 - g * peak * S, 0.55, 1.0)
                Tf = 1.0 - feather * (1.0 - T)
                o[:, :, ci] = np.clip(np.minimum(ch / np.maximum(Tf, 1e-6),
                                                 np.maximum(bgs[key], ch)), 0, 255)
            out.append(o.astype(np.uint8))
        return out

    def fdip(f):
        g = f.astype(np.float64).mean(axis=2)
        bg = bg_full(g, cx, cy)
        if bg is None:
            return float("nan")
        den = float(np.median(bg[disk]))
        if den < 8.0:
            return float("nan")
        return (1 - float(np.median(g[disk])) / den) * 100

    fb = fuse(imgs)
    d0 = fdip(fb)
    if not np.isfinite(d0):
        return dict(stem=stem, note="night")
    d1 = fdip(fuse(corrected(G1)))
    d2 = fdip(fuse(corrected(G2)))
    if np.isfinite(d1) and np.isfinite(d2) and abs(d1 - d2) > 1e-6:
        gstar = G1 + (G2 - G1) * d1 / (d1 - d2)
    else:
        gstar = G1
    gstar = float(min(max(gstar, GCLAMP[0]), GCLAMP[1]))
    fc = fuse(corrected(gstar))
    dfin = fdip(fc)
    if np.isfinite(dfin) and d0 > 1.0 and abs(dfin) > abs(d0) + 0.3:
        return dict(stem=stem, note="worsened", before=round(d0, 2), after=round(dfin, 2))
    dd, hhmm = an
    os.makedirs(f"{OUT}/{dd}", exist_ok=True)
    cv2.imwrite(f"{OUT}/{dd}/{hhmm}_sky.jpg", fc, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
    return dict(stem=stem, note="written", arch=f"{dd}/{hhmm}_sky.jpg",
                before=round(d0, 3), after=round(dfin, 3), gain=round(gstar, 3))


def work(item):
    try:
        return _work(item)
    except Exception as e:
        return dict(stem=item[0], note=f"ERR {str(e)[:70]}")


if __name__ == "__main__":
    stems = {}
    for f in glob.glob(f"{BR}/*.png"):
        b = os.path.basename(f)
        stems.setdefault(b[:15], {})[b[16:19]] = f
    items = sorted((k, v) for k, v in stems.items() if len(v) == 3)
    if len(sys.argv) > 1:                      # optional day filter, comma-separated
        allow = set(sys.argv[1].split(","))
        items = [i for i in items if i[0][:8] in allow]
    nproc = int(os.environ.get("NPROC", "30"))
    print(f"backfilling {len(items)} triples on {nproc} workers", flush=True)
    res = []
    with Pool(nproc) as pool:
        for i, r in enumerate(pool.imap_unordered(work, items, chunksize=2), 1):
            res.append(r)
            if i % 100 == 0:
                print(f"  {i}/{len(items)}", flush=True)
    import collections
    notes = collections.Counter(r["note"] for r in res)
    print(dict(notes), flush=True)
    with open(f"{OUT}/backfill_log.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["stem", "note", "arch", "before", "after", "gain"],
                           extrasaction="ignore")
        w.writeheader()
        for r in sorted(res, key=lambda x: x["stem"]):
            w.writerow(r)
    wr = [r for r in res if r["note"] == "written"]
    if wr:
        b = np.array([r["before"] for r in wr]); a = np.array([r["after"] for r in wr])
        print(f"written {len(wr)}: before med {np.median(b):.2f}% -> after med {np.median(a):.2f}%")
    print("DONE -> ~/corrected", flush=True)
