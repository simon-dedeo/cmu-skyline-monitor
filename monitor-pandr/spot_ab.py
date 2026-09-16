#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""spot_ab.py — ongoing MATCHED A/B verification of the live spot corrector.

Why this exists: the original A/B (archive_precorrect/ vs archive_corrected/, summarised in
archive_corrected/backfill_log.csv) STOPPED on 2026-07-27, so from then until 2026-08-18 nothing
measured whether the correction still worked -- a silent-decay risk on a public site. Measuring the
residual on served frames alone does NOT substitute: enhance()'s CLAHE amplifies local texture, so
control patches scatter +/-12-15% and swamp a sub-1% residual.

Method (a reconstruction of the July metric, so numbers are comparable but not bit-identical):
for each sampled bracket triple, fuse it TWICE through the real pipeline -- Mertens + hdrfuse.enhance
-- once with flatfield._correct() applied per bracket and once without, then measure the DISPLAY dip
at the blemish: median of the r<25 core against a quadratic fit of the r=105-130 annulus, in linear
light, clipped to the frame (the blemish sits ~100 px from the top edge).

The corrector is imported from a SANDBOX COPY so its per-tick provenance/lock side-effects can never
write into the production spot_provenance.csv or .spot_lock.json -- that file is cited in the paper.
The sandbox copies the LIVE model + tmap each run, so what is measured is the production corrector.

Appends one row per frame to spot_lab/ab_log.csv and prints a summary. Fail-soft throughout.
Run nightly from nightly.sh, or by hand:  venv/bin/python3 spot_ab.py [N_TRIPLES]
"""
import os, sys, glob, csv, json, shutil, datetime, collections
import numpy as np, cv2

M   = os.path.expanduser("~/monitor")
BR  = os.path.join(M, "spot_lab", "brackets")
OUT = os.path.join(M, "spot_lab", "ab_log.csv")
SB  = "/tmp/spot_ab_sandbox"
HOURS_BACK = 26
CORE_R, RING0, RING1 = 25, 105, 130
CLEAN_TOL = 0.06                      # ring roughness/level below which the sky is "clean"


def _sandbox():
    """Import flatfield from a throwaway dir so provenance/lock writes stay out of production."""
    shutil.rmtree(SB, ignore_errors=True)
    os.makedirs(SB)
    for f in ("flatfield.py", "spot_model.json", "spot_tmap.npy"):
        shutil.copy2(os.path.join(M, f), os.path.join(SB, f))
    open(os.path.join(SB, ".spot_apply_on"), "w").close()   # ungated inside the sandbox only
    sys.path.insert(0, SB)
    sys.path.append(M)                                      # for hdrfuse
    import flatfield
    if os.path.dirname(os.path.abspath(flatfield.__file__)) != SB:
        raise RuntimeError("sandbox import failed -- refusing to run against production")
    return flatfield


def _srgb_lin(v):
    v = np.asarray(v, np.float64) / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def dip_pct(img, cx, cy):
    """(display dip %, ring roughness/level). + = darker than the surrounding sky."""
    H, W = img.shape[:2]
    y0, y1 = max(0, int(cy - RING1 - 5)), min(H, int(cy + RING1 + 5))
    x0, x1 = max(0, int(cx - RING1 - 5)), min(W, int(cx + RING1 + 5))
    win = img[y0:y1, x0:x1].astype(np.float64)
    if win.size == 0:
        return None, None
    rx, ry = cx - x0, cy - y0
    yy, xx = np.mgrid[0:win.shape[0], 0:win.shape[1]]
    rb = np.sqrt((xx - rx) ** 2 + (yy - ry) ** 2)
    ring, core = (rb >= RING0) & (rb < RING1), rb < CORE_R
    if ring.sum() < 800 or core.sum() < 300:
        return None, None
    lin = _srgb_lin(win.mean(axis=2))
    ay, ax = np.where(ring)
    A = np.column_stack([np.ones_like(ax), ax - rx, ay - ry, (ax - rx) ** 2,
                         (ax - rx) * (ay - ry), (ay - ry) ** 2]).astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, lin[ring], rcond=None)
    rough = float(np.median(np.abs(lin[ring] - A @ coef)))
    cyy, cxx = np.where(core)
    Ac = np.column_stack([np.ones_like(cxx), cxx - rx, cyy - ry, (cxx - rx) ** 2,
                          (cxx - rx) * (cyy - ry), (cyy - ry) ** 2]).astype(np.float64)
    bg = float(np.median(Ac @ coef))
    obs = float(np.median(lin[core]))
    if bg <= 1e-6:
        return None, None
    return 100.0 * (1.0 - obs / bg), rough / bg


def triples():
    """{stamp: [p_br0, p_br1, ...]} for the last HOURS_BACK hours, complete triples only."""
    cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=HOURS_BACK)
    g = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(BR, "*_br*.png"))):
        b = os.path.basename(p)
        try:
            t = datetime.datetime.strptime(b[:15], "%Y%m%d_%H%M%S").replace(
                tzinfo=datetime.timezone.utc)
        except Exception:
            continue
        if t >= cut:
            g[b[:15]].append(p)
    return {k: sorted(v) for k, v in g.items() if len(v) >= 3}


def main():
    n_want = int(sys.argv[1]) if len(sys.argv) > 1 else 36
    ff = _sandbox()
    import hdrfuse
    prov = os.path.join(SB, "spot_provenance.csv")

    tr = triples()
    stamps = sorted(tr)
    if not stamps:
        print("spot_ab: no complete bracket triples in the last %dh" % HOURS_BACK)
        return 0
    stamps = stamps[::max(1, len(stamps) // n_want)][:n_want]
    print("spot_ab: %d triples sampled from %d available" % (len(stamps), len(tr)))

    rows = []
    for s in stamps:
        imgs = [im for im in (cv2.imread(p) for p in tr[s]) if im is not None]
        if len(imgs) < 2:
            continue
        shape = imgs[0].shape
        imgs = [im for im in imgs if im.shape == shape]
        # --- arm A: uncorrected ---
        before_fused = hdrfuse.enhance(
            np.clip(cv2.createMergeMertens().process(imgs) * 255.0, 0, 255).astype(np.uint8))
        # --- arm B: corrected, exactly as production does it (per raw bracket) ---
        pre = sum(1 for _ in open(prov)) if os.path.exists(prov) else 0
        try:
            cimgs = [ff._correct(im) for im in imgs]
        except Exception as e:
            print("  %s: correct FAILED (%s)" % (s, e)); continue
        after_fused = hdrfuse.enhance(
            np.clip(cv2.createMergeMertens().process(cimgs) * 255.0, 0, 255).astype(np.uint8))
        # position the corrector itself used (median over the triple's provenance rows)
        cx = cy = None; locks = 0
        if os.path.exists(prov):
            pr = list(csv.DictReader(open(prov)))[pre - 1 if pre else 0:]
            xs = [float(r["cx"]) for r in pr if r.get("cx")]
            ys = [float(r["cy"]) for r in pr if r.get("cy")]
            locks = sum(int(r.get("lock") or 0) for r in pr)
            if xs and ys:
                cx, cy = float(np.median(xs)), float(np.median(ys))
        if cx is None:
            continue
        b, rb_ = dip_pct(before_fused, cx, cy)
        a, ra_ = dip_pct(after_fused, cx, cy)
        if b is None or a is None:
            continue
        lvl = float(np.median(before_fused))
        rows.append({"stamp": s, "cx": round(cx, 1), "cy": round(cy, 1),
                     "n_brackets": len(imgs), "locks": locks, "level": round(lvl, 1),
                     "before": round(b, 3), "after": round(a, 3),
                     "reduction": round(b - a, 3),
                     "ring_rough": round(rb_, 4),
                     "clean": int(rb_ <= CLEAN_TOL)})
        print("  %s lvl%6.1f  before %+7.3f%%  after %+7.3f%%  locks %d/%d  rough %.3f%s"
              % (s, lvl, b, a, locks, len(imgs), rb_, "" if rb_ <= CLEAN_TOL else "  (textured)"))

    if not rows:
        print("spot_ab: no measurable frames"); return 0
    hdr = not os.path.exists(OUT)
    with open(OUT, "a") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        if hdr: w.writeheader()
        w.writerows(rows)

    import statistics as st
    clean = [r for r in rows if r["clean"] and r["level"] >= 60]
    print("\nwrote %d rows -> %s" % (len(rows), OUT))
    if clean:
        b = [r["before"] for r in clean]; a = [r["after"] for r in clean]
        print("CLEAN DAYLIGHT SUMMARY (n=%d):" % len(clean))
        print("  display dip before: median %+.3f%%" % st.median(b))
        print("  display dip after : median %+.3f%%" % st.median(a))
        print("  reduction         : median %.2fx" % (st.median(b) / max(st.median(a), 1e-6)))
        print("  frames <= 0.3%% target: %d/%d (%.0f%%)"
              % (sum(1 for x in a if x <= 0.3), len(a), 100 * sum(1 for x in a if x <= 0.3) / len(a)))
        worse = sum(1 for r in clean if r["after"] > r["before"])
        print("  made WORSE: %d/%d (%.1f%%)" % (worse, len(clean), 100 * worse / len(clean)))
    else:
        print("no clean daylight frames in this sample (overcast?) — rows still logged")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("spot_ab: FAILED soft:", e, file=sys.stderr)
        sys.exit(0)
