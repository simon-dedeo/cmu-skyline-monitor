"""Synthetic test v2: build the frame by multiplying a smooth linear sky by the PUBLISHED
map's own (scurve-scaled, position-warped) transmittance -- i.e., a frame whose defect is
exactly what the corrector believes. apply() must null it. Tests geometry/warp/sign/scale
consistency end to end; depth fidelity on real frames is covered by the paired tests."""
import sys, os, json, datetime, numpy as np, cv2
sys.path.insert(0, os.path.expanduser("~/monitor"))
import flatfield

mm = json.load(open(os.path.expanduser("~/monitor/spot_model.json")))
sp = mm["spots"][0]; vel = mm.get("velocity", [0, 0]); tmm = mm["tmap"]
fe = datetime.datetime.fromisoformat(mm["fit_epoch"])
now = datetime.datetime.now(datetime.timezone.utc)
dt = min(max((now - fe).total_seconds() / 86400.0, -0.5), 3.5)
px = sp["fx"] * 3840 + vel[0] * dt
py = sp["fy"] * 2160 + vel[1] * dt
T = np.load(os.path.expanduser("~/monitor/spot_tmap.npy")).astype(np.float64)
h = tmm["half"]; x0, y0 = tmm["x0"], tmm["y0"]
rx, ry = tmm.get("mx", h), tmm.get("my", h)
sx, sy = px - (x0 + rx), py - (y0 + ry)
M2 = np.float32([[1, 0, sx], [0, 1, sy]])
Tw = np.stack([cv2.warpAffine(T[:, :, c], M2, (2*h, 2*h), flags=cv2.INTER_LINEAR,
               borderMode=cv2.BORDER_CONSTANT, borderValue=1.0) for c in range(3)], axis=2)

H, W = 2160, 3840
gy, gx = np.mgrid[0:H, 0:W].astype(np.float32)
Llin = 0.55 + 0.10 * (gy / H) - 0.05 * (gx / W)
enc0 = np.where(Llin <= 0.0031308, 12.92*Llin, 1.055*np.clip(Llin,0,1)**(1/2.4)-0.055)*255.0
# scurve scale at this frame's ring level, per channel, exactly as flatfield computes it
sc = mm.get("scurve")
img = np.repeat(enc0[:, :, None], 3, axis=2)
yy, xx = np.mgrid[0:2*h, 0:2*h]
ring = (np.hypot(xx-rx, yy-ry) >= 105) & (np.hypot(xx-rx, yy-ry) < 130)
chn = "bgr"
for c in range(3):
    Dref = 1.0 - Tw[:, :, c]
    scale = 1.0
    if sc:
        L = float(np.median(img[y0:y0+2*h, x0:x0+2*h, c][ring]))
        s_L = float(np.interp(L, sc["level_knots"], sc["s"][chn[c]]))
        s_ref = sc.get("s_ref", {}).get(chn[c]) or s_L
        scale = min(max(s_L / max(s_ref, 1e-3), 0.3), 4.0)
    Tc = np.clip(1.0 - Dref * scale, 0.5, 1.05)
    lin = Llin.copy()
    lin[y0:y0+2*h, x0:x0+2*h] *= np.clip(Tc, 0.7, 1.0)
    img[:, :, c] = np.where(lin <= 0.0031308, 12.92*lin,
                            1.055*np.clip(lin,0,1)**(1/2.4)-0.055)*255.0
img = np.clip(img, 0, 255).astype(np.uint8)

out = flatfield.apply(img)
def lin(u):
    v = u.astype(np.float64)/255.0
    return np.where(v <= 0.04045, v/12.92, ((v+0.055)/1.055)**2.4)
rr2 = (gx-px)**2 + (gy-py)**2
disk = rr2 < 30**2; ringf = (rr2 >= 105**2) & (rr2 < 130**2)
for name, im in (("before", img), ("after", out)):
    L = lin(im[:, :, 1])
    print(f"{name}: dip at predicted position = {(1-np.median(L[disk])/np.median(L[ringf]))*100:+.3f}% linear")
La = lin(out[:, :, 1])
resid = (1 - np.median(La[disk]) / np.median(La[ringf])) * 100
print("PASS" if abs(resid) < 0.5 else "FAIL", f"(threshold 0.5%, got {resid:+.3f}%)")
