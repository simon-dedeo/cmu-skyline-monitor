"""Machine-readable paired live evidence: for every complete raw triple in the given UTC
windows, re-fuse WITHOUT correction and compare against the SERVED frame at the
velocity-predicted centre. Emits CSV with full provenance for the paper."""
import glob, os, sys, json, csv, datetime, numpy as np, cv2
sys.path.insert(0, os.path.expanduser("~/monitor"))
import hdrfuse
cv2.setNumThreads(4)

mm = json.load(open(os.path.expanduser("~/monitor/spot_model.json")))
sp = mm["spots"][0]; vel = mm.get("velocity", [0, 0])
fe = datetime.datetime.fromisoformat(mm["fit_epoch"])
R0, R1, TOP = 110, 170, 20
WINDOWS = [("20260727", "2200", "2359"), ("20260728", "1230", "2359")]

def load3(p):
    im = cv2.imread(p, cv2.IMREAD_UNCHANGED)
    return None if im is None else (im[:, :, :3] if im.ndim == 3 and im.shape[2] > 3 else im)

def centre_at(ts):
    dt = (ts - fe).total_seconds() / 86400.0
    return sp["fx"]*3840 + vel[0]*dt, sp["fy"]*2160 + vel[1]*dt

def stats(g, cx, cy):
    H, W = g.shape
    y0, y1 = max(TOP, int(cy)-230), min(H, int(cy)+230)
    x0, x1 = max(0, int(cx)-230), min(W, int(cx)+230)
    sub = g[y0:y1, x0:x1]; gy, gx = np.mgrid[y0:y1, x0:x1]
    rr = np.hypot(gx-cx, gy-cy)
    ring = (rr>=R0)&(rr<R1)&(gy>=TOP); disk = rr < 40
    un, vn = (gx[ring]-cx)/100.0, (gy[ring]-cy)/100.0
    A = np.column_stack([np.ones(un.size), un, vn, un**2, un*vn, vn**2])
    vals = sub[ring].astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
    res = vals - A@coef; mad = np.median(np.abs(res-np.median(res)))+1e-9
    keep = np.abs(res) < 4*1.4826*mad
    if keep.sum() > 150: coef, *_ = np.linalg.lstsq(A[keep], vals[keep], rcond=None)
    un2, vn2 = (gx[disk]-cx)/100.0, (gy[disk]-cy)/100.0
    bg = coef[0]+coef[1]*un2+coef[2]*vn2+coef[3]*un2**2+coef[4]*un2*vn2+coef[5]*vn2**2
    ratio = sub/np.maximum(cv2.GaussianBlur(sub.astype(np.float32),(0,0),60),1e-3)
    struct = float(np.std(ratio[ring]))*100
    den = float(np.median(bg))
    dip = (1-float(np.median(sub[disk]))/den)*100 if den > 20 else float("nan")
    return dip, struct, float(np.median(sub))

BR = os.path.expanduser("~/monitor/spot_lab/brackets")
rows = []
for day8, t0, t1 in WINDOWS:
    stems = {}
    for f in glob.glob(f"{BR}/{day8}_*.png"):
        b = os.path.basename(f); stems.setdefault(b[:15], {})[b[16:19]] = f
    for stem in sorted(k for k, v in stems.items() if len(v) == 3):
        hhmm = stem[9:13]
        if not (t0 <= hhmm <= t1):
            continue
        day = f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}"
        mins = int(hhmm[:2])*60+int(hhmm[2:])
        served = None
        for m in range(mins-3, mins+1):
            p = os.path.expanduser(f"~/monitor/archive/{day}/{m//60:02d}{m%60:02d}_sky.jpg")
            if os.path.exists(p): served = p
        if not served:
            continue
        ts = datetime.datetime.strptime(stem, "%Y%m%d_%H%M%S").replace(tzinfo=datetime.timezone.utc)
        cx, cy = centre_at(ts)
        sv = cv2.imread(served)
        d_sv, struct, lvl = stats(sv.astype(np.float32).mean(axis=2), cx, cy)
        imgs = [load3(stems[stem][b]) for b in ("br0","br1","br2")]
        if any(i is None for i in imgs): continue
        fu = cv2.createMergeMertens().process([np.ascontiguousarray(i) for i in imgs])
        fu = hdrfuse.enhance(np.clip(fu*255.0,0,255).astype(np.uint8))
        d_un, _, _ = stats(fu.astype(np.float32).mean(axis=2), cx, cy)
        rows.append(dict(stem=stem, served=os.path.basename(served), lvl=round(lvl,1),
                         ring_struct=round(struct,2), cx=round(cx,1), cy=round(cy,1),
                         uncorrected=round(d_un,3), served_dip=round(d_sv,3),
                         model_updated=mm["updated"]))
        print(f"{stem} struct={struct:.2f} unc={d_un:+.2f} served={d_sv:+.2f}", flush=True)
with open("/tmp/live_paired.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader(); [w.writerow(r) for r in rows]
clean = [r for r in rows if r["ring_struct"] < 3.0 and 1.0 <= r["uncorrected"] <= 9.0 and r["lvl"] >= 140]
if clean:
    u = np.array([r["uncorrected"] for r in clean]); s = np.array([r["served_dip"] for r in clean])
    rm = 1 - s/u
    print(f"\nCLEAN subset n={len(clean)}: removal med {np.median(rm)*100:.1f}%  "
          f"served med {np.median(s):.2f}%  range [{s.min():.2f},{s.max():.2f}]")
print(f"all rows: {len(rows)} -> /tmp/live_paired.csv")
