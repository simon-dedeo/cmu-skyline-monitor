"""CmdStan JSON for spotpos2 from the cached +/-150 per-day maps (/tmp/spotpos)."""
import csv, json, sys, os, numpy as np
B, DS, HALF = "/tmp/spotpos", int(sys.argv[1]) if len(sys.argv) > 1 else 8, 150.0
pri = {r["day"]: r for r in csv.DictReader(open(f"{B}/priors.csv"))}
U, V, Y, X, start, ln, dayl, resid = [], [], [], [], [], [], [], []
for day in sorted(pri):
    p = f"{B}/{day}_map.npy"
    if not os.path.exists(p):
        continue
    r = pri[day]; pcx, pcy = int(r["pcx"]), int(r["pcy"])
    x_lo, y_lo = int(r["x_lo"]), int(r["y_lo"])
    A = np.load(p)
    h, w = (A.shape[0] // DS) * DS, (A.shape[1] // DS) * DS
    Dn = A[:h, :w].reshape(h // DS, DS, w // DS, DS).mean(axis=(1, 3))
    gy, gx = np.mgrid[0:Dn.shape[0], 0:Dn.shape[1]]
    u = (gx * DS + x_lo + DS / 2 - pcx).ravel().astype(float)
    v = (gy * DS + y_lo + DS / 2 - pcy).ravel().astype(float)
    un, vn = u / HALF, v / HALF
    basis = np.column_stack([un, vn, un**2, un*vn, vn**2])
    basis = (basis - basis.mean(0)) / basis.std(0)
    start.append(len(Y) + 1); ln.append(Dn.size); dayl.append(day)
    U += u.tolist(); V += v.tolist(); Y += Dn.ravel().astype(float).tolist(); X += basis.tolist()
    resid.append(float(np.std(Dn - np.median(Dn))))
dat = dict(N=len(Y), M=len(start), start=start, len=ln, u=U, v=V, X=X, y=Y,
           pos_sd=30.0, step_sd=6.0, noise_scale=round(float(np.median(resid)), 5))
json.dump(dat, open(f"{B}/stan_data2.json", "w"))
json.dump(dayl, open(f"{B}/days2.json", "w"))
print(f"N={dat['N']} M={dat['M']} per_map={ln[0]} noise_scale={dat['noise_scale']} days={dayl}")
