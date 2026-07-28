"""Emit CmdStan JSON for spotell over a chosen set of days (a rolling window)."""
import csv, json, sys, os, numpy as np
CS, HALF = "/tmp/clearstack", 150.0
DS = 8
days_want = sys.argv[1].split(",")
outp = sys.argv[2]
rows = [r for r in csv.DictReader(open(f"{CS}/stacks.csv")) if r["day"] in days_want
        and os.path.exists(f"{CS}/{r['day']}_{r['bucket']}.npy")]
days = sorted({r["day"] for r in rows}); bkts = sorted({r["bucket"] for r in rows})
U=V=None; U,V,Y,X,start,ln,day_of,bkt_of,resid,meta = [],[],[],[],[],[],[],[],[],[]
for r in rows:
    pcx,pcy,x_lo,y_lo = int(r["pcx"]),int(r["pcy"]),int(r["x_lo"]),int(r["y_lo"])
    A = np.load(f"{CS}/{r['day']}_{r['bucket']}.npy").astype(np.float64)
    h,w = (A.shape[0]//DS)*DS, (A.shape[1]//DS)*DS
    Dn = A[:h,:w].reshape(h//DS,DS,w//DS,DS).mean(axis=(1,3))
    gy,gx = np.mgrid[0:Dn.shape[0],0:Dn.shape[1]]
    u = (gx*DS + x_lo + DS/2 - pcx).ravel().astype(float)
    v = (gy*DS + y_lo + DS/2 - pcy).ravel().astype(float)
    un,vn = u/HALF, v/HALF
    B = np.column_stack([un,vn,un**2,un*vn,vn**2]); B = (B-B.mean(0))/B.std(0)
    start.append(len(Y)+1); ln.append(Dn.size)
    day_of.append(days.index(r["day"])+1); bkt_of.append(bkts.index(r["bucket"])+1)
    U+=u.tolist(); V+=v.tolist(); Y+=Dn.ravel().astype(float).tolist(); X+=B.tolist()
    resid.append(float(np.std(Dn-np.median(Dn))))
    meta.append(dict(day=r["day"], bucket=r["bucket"], pcx=pcx, pcy=pcy,
                     x_lo=x_lo, y_lo=y_lo, n=int(r["n"])))
dat = dict(N=len(Y), M=len(rows), D=len(days), K=len(bkts), start=start, len=ln,
           day_of=day_of, bkt_of=bkt_of, u=U, v=V, X=X, y=Y,
           pos_sd=30.0, step_sd=15.0, noise_scale=round(float(np.median(resid)),5))
json.dump(dat, open(outp,"w"))
json.dump(dict(days=days, buckets=bkts, maps=meta), open(outp+".idx","w"), indent=1)
print(f"N={dat['N']} M={dat['M']} D={dat['D']} K={dat['K']} days={days} noise={dat['noise_scale']}")
