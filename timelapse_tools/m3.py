import os, glob, csv, datetime as dt, numpy as np, cv2, statistics as st
HERE = os.path.expanduser("~/monitor")

def srgb_lin(v):
    v = np.asarray(v, np.float64)/255.0
    return np.where(v <= 0.04045, v/12.92, ((v+0.055)/1.055)**2.4)

prov = {}
for r in csv.DictReader(open(os.path.join(HERE,"spot_provenance.csv"))):
    try: prov[dt.datetime.fromisoformat(r["ts"])] = (float(r["cx"]), float(r["cy"]))
    except Exception: pass
pkeys = sorted(prov)

def centre_for(day, hhmm):
    t0 = dt.datetime.fromisoformat("%sT%s:%s:00+00:00"%(day,hhmm[:2],hhmm[2:]))
    best, bd = None, 9e9
    for k in pkeys:
        d = abs((k-t0).total_seconds())
        if d < bd: best, bd = prov[k], d
    return best if bd < 900 else None

def dip_pct(img, cx, cy):
    H, W = img.shape[:2]
    y0, y1 = max(0,int(cy-135)), min(H,int(cy+135))
    x0, x1 = max(0,int(cx-135)), min(W,int(cx+135))
    win = img[y0:y1, x0:x1].astype(np.float64)
    if win.size == 0: return None, None
    rx, ry = cx-x0, cy-y0
    yy, xx = np.mgrid[0:win.shape[0], 0:win.shape[1]]
    rb = np.sqrt((xx-rx)**2 + (yy-ry)**2)
    ring, core = (rb>=105)&(rb<130), rb<25
    if ring.sum() < 800 or core.sum() < 300: return None, None
    lin = srgb_lin(win.mean(axis=2))
    ay, ax = np.where(ring)
    A = np.column_stack([np.ones_like(ax), ax-rx, ay-ry,
                         (ax-rx)**2, (ax-rx)*(ay-ry), (ay-ry)**2]).astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, lin[ring], rcond=None)
    rough = float(np.median(np.abs(lin[ring]-A@coef)))
    cyy, cxx = np.where(core)
    Ac = np.column_stack([np.ones_like(cxx), cxx-rx, cyy-ry,
                          (cxx-rx)**2, (cxx-rx)*(cyy-ry), (cyy-ry)**2]).astype(np.float64)
    bg, obs = float(np.median(Ac@coef)), float(np.median(lin[core]))
    if bg <= 1e-6: return None, None
    return 100.0*(1.0-obs/bg), rough/bg

def q(v,p):
    v=sorted(v); return v[int(p*(len(v)-1))]

def scan(label, pat, controls=False, rmax=0.25):
    out, ctl, RG, nr = [], [], [], 0
    files = sorted(glob.glob(pat))
    def _hour(f):                      # tolerate stray non-HHMM files in these dirs
        b = os.path.basename(f)[:2]
        return int(b) if b.isdigit() else -1
    files = [f for f in files if 14 <= _hour(f) <= 20]
    step = max(1,len(files)//70)
    for f in files[::step]:
        day, hhmm = os.path.basename(os.path.dirname(f)), os.path.basename(f)[:4]
        img = cv2.imread(f)
        if img is None: continue
        c = centre_for(day,hhmm)
        if c is None: continue
        d,rg = dip_pct(img,c[0],c[1])
        if d is None: continue
        if rg > rmax: nr += 1; continue
        out.append(d); RG.append(rg)
        if controls:
            for dx in (-600,600,1000):
                cd,cr = dip_pct(img,c[0]+dx,c[1])
                if cd is not None and cr is not None and cr<=rmax: ctl.append(cd)
    def s(v):
        if not v: return "no usable frames"
        return "n=%3d  median %+.3f%%  p10 %+.3f%%  p90 %+.3f%%  max %+.3f%%"%(
            len(v),st.median(v),q(v,.1),q(v,.9),q(v,1.0))
    print("%-36s %s"%(label,s(out)))
    if RG: print("%-36s   ring roughness rel-bg: median %.3f p90 %.3f  (%d skipped as too rough)"%("",st.median(RG),q(RG,.9),nr))
    if controls: print("%-36s %s"%("  control patches, same frames",s(ctl)))
    return out

print("dip at blemish: core r<25 vs quadratic ring fit, LINEAR light, midday (14-20Z)")
print("(+ = darker than surrounding sky; display target <= 0.3%)\n")
scan("LIVE served Aug 12-18 (corrected)", os.path.join(HERE,"archive","2026-08-1[2-8]","*_sky.jpg"), controls=True)
print()
scan("LIVE served Aug 17-18 (corrected)", os.path.join(HERE,"archive","2026-08-1[78]","*_sky.jpg"))
print()
scan("uncorrected reference Aug 12-18", os.path.join(HERE,"archive_ref","2026-08-1[2-8]","*.jpg"))
