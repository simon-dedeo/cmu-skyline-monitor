"""Build CmdStan JSON straight from the cached per-(day,bracket) maps:
contiguous blocks, per-map standardised background basis, drift-relative coords."""
import csv, json, sys, os, numpy as np
B, DS, HALF = "/tmp/spothier", int(sys.argv[1]) if len(sys.argv) > 1 else 12, 110.0
maps = [m for m in csv.DictReader(open(f"{B}/maps.csv"))
        if os.path.exists(f"{B}/{m['day']}_{m['bucket']}.npy")]
days = sorted({m["day"] for m in maps}); bkts = sorted({m["bucket"] for m in maps})
U, V, Y, X, start, ln, day_of, bkt_of = [], [], [], [], [], [], [], []
resid = []
for m in maps:
    A = np.load(f"{B}/{m['day']}_{m['bucket']}.npy")
    h, w = (A.shape[0] // DS) * DS, (A.shape[1] // DS) * DS
    Dn = A[:h, :w].reshape(h // DS, DS, w // DS, DS).mean(axis=(1, 3))
    gy, gx = np.mgrid[0:Dn.shape[0], 0:Dn.shape[1]]
    u = (gx * DS + int(m["x_lo"]) + DS / 2 - int(m["pcx"])).ravel().astype(float)
    v = (gy * DS + int(m["y_lo"]) + DS / 2 - int(m["pcy"])).ravel().astype(float)
    un, vn = u / HALF, v / HALF
    basis = np.column_stack([un, vn, un**2, un*vn, vn**2])
    basis = (basis - basis.mean(0)) / basis.std(0)          # standardise per map
    start.append(len(Y) + 1); ln.append(Dn.size)
    day_of.append(days.index(m["day"]) + 1); bkt_of.append(bkts.index(m["bucket"]) + 1)
    U += u.tolist(); V += v.tolist(); Y += Dn.ravel().astype(float).tolist()
    X += basis.tolist()
    resid.append(float(np.std(Dn - np.median(Dn))))
dat = dict(N=len(Y), M=len(maps), D=len(days), K=len(bkts), start=start, len=ln,
           day_of=day_of, bkt_of=bkt_of, u=U, v=V, X=X, y=Y,
           pos_sd=30.0, step_sd=6.0, amp_mu=0.02, amp_sd=0.015,
           sig_mu=35.0, sig_sd=8.0, noise_scale=round(float(np.median(resid)), 5))
json.dump(dat, open(f"{B}/stan_data.json", "w"))
print(f"N={dat['N']} M={dat['M']} D={dat['D']} K={dat['K']} per_map={ln[0]} "
      f"noise_scale={dat['noise_scale']}")
json.dump(dict(days=days, buckets=bkts), open(f"{B}/index.json", "w"))
