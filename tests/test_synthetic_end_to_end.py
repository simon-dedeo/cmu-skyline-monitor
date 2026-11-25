"""Synthetic end-to-end test for flatfield.py (schema 3 CRF + Bayesian MAP).

Injects the published transmittance map (warped to the predicted position) into a smooth
synthetic 3-bracket HDR set in empirical-CRF linear radiance, runs flatfield.correct_set(),
and verifies that all 3 channels are nulled to < 0.5% linear residual.
"""
import sys, os, json, datetime, numpy as np, cv2

HERE = os.path.dirname(os.path.abspath(__file__))
PANDR_DIR = os.environ.get("SPOT_TEST_DIR", os.path.expanduser("~/monitor"))
sys.path.insert(0, PANDR_DIR)
import flatfield

mm = json.load(open(os.path.join(PANDR_DIR, "spot_model.json")))
sp = mm["spots"][0]; vel = mm.get("velocity", [0, 0]); tmm = mm["tmap"]
fe = datetime.datetime.fromisoformat(mm["fit_epoch"])
now = datetime.datetime.now(datetime.timezone.utc)
dt = min(max((now - fe).total_seconds() / 86400.0, -0.5), 3.5)
px = sp["fx"] * 3840 + vel[0] * dt
py = sp["fy"] * 2160 + vel[1] * dt

T = np.load(os.path.join(PANDR_DIR, "spot_tmap.npy")).astype(np.float64)
h = int(tmm["half"]); x0, y0 = int(tmm["x0"]), int(tmm["y0"])
rx, ry = float(tmm.get("mx", h)), float(tmm.get("my", h))
sx, sy = px - (x0 + rx), py - (y0 + ry)
M2 = np.float32([[1, 0, sx], [0, 1, sy]])
Tw = np.stack([cv2.warpAffine(T[:, :, c], M2, (2 * h, 2 * h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=1.0)
               for c in range(3)], axis=2)

H, W = 2160, 3840
gy, gx = np.mgrid[0:H, 0:W].astype(np.float32)
crf = mm.get("crf") or flatfield.DEFAULT_CRF

# Build 3 synthetic exposure brackets (0.5x, 1.0x, 2.0x) with smooth sky + injected spot
imgs = []
for gain_br in (0.35, 0.58, 0.82):
    Llin = np.clip(gain_br + 0.08 * (gy / H) - 0.04 * (gx / W), 0.05, 0.95)
    im = np.zeros((H, W, 3), dtype=np.float64)
    for c, ch in enumerate("bgr"):
        a, g_exp = crf[ch]
        L = Llin.copy()
        L[y0:y0 + 2 * h, x0:x0 + 2 * h] *= np.clip(Tw[:, :, c], 0.80, 1.0)
        im[:, :, c] = flatfield._crf_inv(L, a, g_exp)
    imgs.append(np.rint(np.clip(im, 0, 255)).astype(np.uint8))

os.environ["SPOT_NO_LOCK"] = "1"
out = flatfield.correct_set(imgs)
os.environ.pop("SPOT_NO_LOCK", None)

rr2 = (gx - px) ** 2 + (gy - py) ** 2
disk = (rr2 < 25 ** 2) & (gy >= 20)
Ltrue = np.clip(0.58 + 0.08 * (gy / H) - 0.04 * (gx / W), 0.05, 0.95)
ok = True
for c, ch in enumerate("bgr"):
    a, g_exp = crf[ch]
    Lb = flatfield._crf_eval(imgs[1][:, :, c], a, g_exp)
    La = flatfield._crf_eval(out[1][:, :, c], a, g_exp)
    db = (1.0 - float(np.median(Lb[disk] / Ltrue[disk]))) * 100
    da = (1.0 - float(np.median(La[disk] / Ltrue[disk]))) * 100
    print(f"ch {ch.upper()}: before={db:+.3f}% -> after={da:+.3f}% linear (vs ground truth)")
    if abs(da) >= 0.5:
        ok = False
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
