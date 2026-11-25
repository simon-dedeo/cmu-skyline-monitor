#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""spot_ab.py -- nightly matched A/B verification of the live spot corrector (pandr).

Three arms per bracket triple: uncorrected, corrected as served (with lock), and corrected
with lock disabled (SPOT_NO_LOCK). Measures BOTH:
  * Mertens-only linear dip (true optical removal, per-channel B/G/R + gray, immune to the
    +3% CLAHE tile-seam artifact at cy~68), AND
  * Post-enhance display dip (for continuity with earlier logs).

Runs in a sandbox copy so provenance/lock side-effects never touch production files.
Usage: venv/bin/python3 spot_ab.py [N_TRIPLES]
"""
import os, sys, glob, csv, shutil, datetime, collections, statistics as st
import numpy as np, cv2

M   = os.path.expanduser("~/monitor")
BR  = os.path.join(M, "spot_lab", "brackets")
OUT = os.environ.get("SPOT_AB_OUT", os.path.join(M, "spot_lab", "ab_log.csv"))
SRC = os.environ.get("SPOT_AB_SRC", M)
SB  = os.environ.get("SPOT_AB_SANDBOX", "/tmp/spot_ab_sandbox")
HOURS_BACK = 26
TOP_MASK = 20
CORE_R, RING0, RING1 = 25, 85, 115
RIDGE_QUAD = (50.0, 50.0, 200.0)
CLEAN_TOL = 0.04


def _sandbox():
    shutil.rmtree(SB, ignore_errors=True)
    os.makedirs(SB)
    shutil.copy2(os.path.join(SRC, "flatfield.py"), os.path.join(SB, "flatfield.py"))
    for f in ("spot_model.json", "spot_tmap.npy"):
        shutil.copy2(os.path.join(M, f), os.path.join(SB, f))
    open(os.path.join(SB, ".spot_apply_on"), "w").close()
    sys.path.insert(0, SB)
    sys.path.append(M)
    import flatfield
    if os.path.dirname(os.path.abspath(flatfield.__file__)) != SB:
        raise RuntimeError("sandbox import failed")
    return flatfield


def _srgb_lin(v):
    v = np.asarray(v, np.float64) / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def dip_metrics(img, cx, cy):
    """(gray_dip_pct, [b, g, r]_dip_pct, ring_roughness) with TOP_MASK=20 and ridge sky fit."""
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
    core = (rb < CORE_R) & (yy + y0 >= TOP_MASK)
    if ring.sum() < 500 or core.sum() < 250:
        return None, None, None
    ay, ax = np.where(ring)
    cyy, cxx = np.where(core)
    ur, vr = (ax - rx) / 100.0, (ay - ry) / 100.0
    uc, vc = (cxx - rx) / 100.0, (cyy - ry) / 100.0
    A = np.column_stack([np.ones_like(ur), ur, vr, ur * ur, ur * vr, vr * vr])
    Ac = np.column_stack([np.ones_like(uc), uc, vc, uc * uc, uc * vc, vc * vc])
    pen = RIDGE_QUAD if cy < RING1 + TOP_MASK else (2.0, 2.0, 2.0)
    R = np.diag([0.0, 0.0, 0.0, pen[0], pen[1], pen[2]])

    def _fit_one(lin):
        vals = lin[ring]
        coef = np.linalg.solve(A.T @ A + R, A.T @ vals)
        res = vals - A @ coef
        mad = float(np.median(np.abs(res - np.median(res)))) * 1.4826 + 1e-9
        keep = np.abs(res) < 3.0 * mad
        if keep.sum() > 250:
            Ak = A[keep]
            coef = np.linalg.solve(Ak.T @ Ak + R, Ak.T @ vals[keep])
            res = vals[keep] - Ak @ coef
            mad = float(np.median(np.abs(res - np.median(res)))) * 1.4826 + 1e-9
        bg = float(np.median(Ac @ coef))
        bg_r = float(np.median(A @ coef))
        if bg <= 1e-6 or bg_r <= 1e-6:
            return None, None
        return 100.0 * (1.0 - float(np.median(lin[core])) / bg), mad / bg_r

    gd, rough = _fit_one(_srgb_lin(win.mean(axis=2)))
    if gd is None:
        return None, None, None
    cdips = []
    for c in range(3):
        d, _ = _fit_one(_srgb_lin(win[:, :, c]))
        cdips.append(0.0 if d is None else d)
    return gd, cdips, rough


def triples():
    cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=HOURS_BACK)
    g = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(BR, "*_br*.png"))):
        b = os.path.basename(p)
        try:
            t = datetime.datetime.strptime(b[:15], "%Y%m%d_%H%M%S").replace(tzinfo=datetime.timezone.utc)
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
    merge = cv2.createMergeMertens()

    tr = triples()
    stamps = sorted(tr)
    if not stamps:
        print(f"spot_ab: no complete bracket triples in the last {HOURS_BACK}h")
        return 0
    if len(stamps) > n_want:
        idx = np.linspace(0, len(stamps) - 1, n_want, dtype=int)
        stamps = [stamps[i] for i in idx]
    print(f"spot_ab: {len(stamps)} triples sampled from {len(tr)} available")

    rows = []
    for s in stamps:
        imgs = [im for im in (cv2.imread(p) for p in tr[s]) if im is not None]
        if len(imgs) < 2:
            continue
        shape = imgs[0].shape
        imgs = [im for im in imgs if im.shape == shape]
        m_bef = np.clip(merge.process(imgs) * 255.0, 0, 255).astype(np.uint8)
        e_bef = hdrfuse.enhance(m_bef)

        os.environ["SPOT_NOW"] = datetime.datetime.strptime(s, "%Y%m%d_%H%M%S").replace(
            tzinfo=datetime.timezone.utc).isoformat()
        try:
            cimgs = ff.correct_set(imgs)
            # Read arm B's exact provenance row BEFORE arm C appends its row
            cx = cy = None; locks = 0
            if os.path.exists(prov):
                last = list(csv.DictReader(open(prov)))[-1]
                cx, cy = float(last["cx"]), float(last["cy"])
                locks = int(last.get("lock") or 0)
            os.environ["SPOT_NO_LOCK"] = "1"
            nimgs = ff.correct_set(imgs)
        except Exception as e:
            print(f"  {s}: correct FAILED ({e})")
            continue
        finally:
            os.environ.pop("SPOT_NO_LOCK", None)
            os.environ.pop("SPOT_NOW", None)

        if cx is None:
            continue
        m_aft = np.clip(merge.process(cimgs) * 255.0, 0, 255).astype(np.uint8)
        m_nol = np.clip(merge.process(nimgs) * 255.0, 0, 255).astype(np.uint8)
        e_aft = hdrfuse.enhance(m_aft)
        e_nol = hdrfuse.enhance(m_nol)

        mb, _, rough = dip_metrics(m_bef, cx, cy)
        ma, ca, _ = dip_metrics(m_aft, cx, cy)
        mn, _, _ = dip_metrics(m_nol, cx, cy)
        eb, _, _ = dip_metrics(e_bef, cx, cy)
        ea, _, _ = dip_metrics(e_aft, cx, cy)
        en, _, _ = dip_metrics(e_nol, cx, cy)
        if None in (mb, ma, mn, eb, ea, en):
            continue
        lvl = float(np.median(m_bef))
        clean = int(rough <= CLEAN_TOL)
        rows.append({
            "stamp": s, "cx": round(cx, 1), "cy": round(cy, 1), "n_brackets": len(imgs),
            "locks": locks, "level": round(lvl, 1),
            "m_before": round(mb, 3), "m_after": round(ma, 3), "m_nolock": round(mn, 3),
            "after_b": round(ca[0], 3), "after_g": round(ca[1], 3), "after_r": round(ca[2], 3),
            "before": round(eb, 3), "after": round(ea, 3), "after_nolock": round(en, 3),
            "reduction": round(mb - ma, 3), "ring_rough": round(rough, 4), "clean": clean,
        })
        print(f"  {s} lvl{lvl:5.1f}  Mertens {mb:+6.2f}% -> {ma:+6.2f}% (nl {mn:+6.2f}% BGR[{ca[0]:+.1f},{ca[1]:+.1f},{ca[2]:+.1f}])"
              f"  Enh {eb:+6.2f}% -> {ea:+6.2f}%  lk={locks} rgh={rough:.3f}{'' if clean else ' (tex)'}")

    if not rows:
        print("spot_ab: no measurable frames")
        return 0
    fields = list(rows[0].keys())
    if os.path.exists(OUT):
        with open(OUT) as fh:
            if fh.readline().strip().split(",") != fields:
                shutil.move(OUT, OUT.replace(".csv", "_v2.csv"))
    hdr = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if hdr:
            w.writeheader()
        w.writerows(rows)

    clean_rows = [r for r in rows if r["clean"] and r["level"] >= 50]
    print(f"\nwrote {len(rows)} rows -> {OUT}")
    if clean_rows:
        mb = [r["m_before"] for r in clean_rows]; ma = [r["m_after"] for r in clean_rows]
        mn = [r["m_nolock"] for r in clean_rows]
        eb = [r["before"] for r in clean_rows]; ea = [r["after"] for r in clean_rows]
        print(f"CLEAN DAYLIGHT SUMMARY (n={len(clean_rows)}):")
        print(f"  Mertens linear dip : before {st.median(mb):+.2f}% -> after {st.median(ma):+.2f}% "
              f"(|after| med {st.median([abs(x) for x in ma]):.2f}%, nolock {st.median(mn):+.2f}%)")
        print(f"  Mertens per-ch BGR : [{st.median([r['after_b'] for r in clean_rows]):+.2f}%, "
              f"{st.median([r['after_g'] for r in clean_rows]):+.2f}%, "
              f"{st.median([r['after_r'] for r in clean_rows]):+.2f}%]")
        print(f"  Enhanced display   : before {st.median(eb):+.2f}% -> after {st.median(ea):+.2f}%")
        worse = sum(1 for x, y in zip(mb, ma) if abs(y) > abs(x))
        print(f"  made WORSE (|after|>|before|): {worse}/{len(clean_rows)} ({100*worse/len(clean_rows):.1f}%)")
    else:
        print("no clean daylight frames in this sample (overcast?) -- rows still logged")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("spot_ab: FAILED soft:", e, file=sys.stderr)
        sys.exit(0)
