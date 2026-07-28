"""Per-channel optical signature of the blemish, from the registered linear map:
core depth, half-depth radius, and INTEGRATED deficit per channel. Discriminates a grey
object seen through chromatic defocus (equal integrated deficit, widths varying) from
genuinely chromatic extinction (integrated deficit varying)."""
import os, json, numpy as np, cv2

T = np.load(os.path.expanduser("~/monitor/spot_tmap.npy")).astype(np.float64)
mm = json.load(open(os.path.expanduser("~/monitor/spot_model.json")))
tm = mm["tmap"]
mx, my = tm.get("mx", 150), tm.get("my", 150)
h = tm["half"]
gy, gx = np.mgrid[0:2*h, 0:2*h]
rr = np.hypot(gx - mx, gy - my)
print(f"map n={tm['n']} frames, registered, linear ratios; content at ({mx},{my})")
print(f"{'ch':>3s} {'core depth':>11s} {'r_half':>7s} {'integrated deficit':>19s} {'rel':>6s}")
base = None
for ci, chn in enumerate("bgr"):
    D = 1.0 - T[:, :, ci]
    S = cv2.GaussianBlur(D.astype(np.float32), (0, 0), 4).astype(np.float64)
    core = float(np.percentile(S[rr < 20], 95))
    half = core / 2
    mask = (S >= half) & (rr < 90)
    r_half = np.sqrt(mask.sum() / np.pi)
    integ = float(np.clip(D, 0, None)[rr < 90].sum())
    if base is None:
        base = integ
    print(f"{chn:>3s} {core*100:10.2f}% {r_half:7.1f} {integ:19.1f} {integ/base:6.2f}")
# wavelengths: B~460, G~540, R~610 nm. Rayleigh λ^-4 would give B:G:R = 3.1:1.6:1.0
