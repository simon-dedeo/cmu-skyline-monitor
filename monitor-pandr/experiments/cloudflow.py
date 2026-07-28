#!/usr/bin/env python3
"""
cloudflow.py v2 — cloud-motion -> apparent wind-vector prototype.

Pipeline:
  1. Temporal-median background over the whole sequence  -> the STATIC scene
     (buildings, tower, persistent haze). Subtract it: residual = moving clouds.
  2. Per consecutive pair, phaseCorrelate the Hanning-windowed residuals -> the
     DOMINANT cloud translation (robust to the large ~100 px/5-min displacement
     that defeats Farneback, and to exposure changes since it's frequency-domain).
  3. Downscaled Farneback on the residuals -> a spatially-varying flow FIELD for
     the quiver / HSV visualisation (the "map").
  4. Aggregate the per-pair dominant vectors -> mean cloud-advection bearing +
     apparent speed + a consistency score.

Directions are IMAGE-RELATIVE (bearing clockwise from image-up). Converting to
true compass needs the camera azimuth/orientation (a one-time calibration from
sun position or known landmarks) -- noted in the output, not faked.

Usage: cloudflow.py OUT_DIR frame1.jpg frame2.jpg ...   (time order)
"""
import cv2, numpy as np, sys, os, time, math, json

out_dir = sys.argv[1]; os.makedirs(out_dir, exist_ok=True)
paths = sys.argv[2:]
imgs = [(p, cv2.imread(p)) for p in paths]
imgs = [(p, im) for p, im in imgs if im is not None]
assert len(imgs) >= 2
H, W = imgs[0][1].shape[:2]
SKY_H = int(0.42 * H)
MIN_PER_STEP = 5.0

# grayscale sky ROI, float32
grays = [cv2.cvtColor(im[:SKY_H], cv2.COLOR_BGR2GRAY).astype(np.float32) for _, im in imgs]

# 1. static background = per-pixel temporal median
bg = np.median(np.stack(grays), axis=0)
# residual: positive where a bright cloud sits over the (bluer/darker) median sky
resid = [np.clip(g - bg, 0, 255) for g in grays]
# cloud mask (for the field viz + reliability): residual is meaningfully bright
def cmask(r): return r > 12

han = cv2.createHanningWindow((W, SKY_H), cv2.CV_32F)

# 2. phaseCorrelate per pair on windowed residuals -> dominant translation
t0 = time.time()
pc_vecs = []
for a, b in zip(resid[:-1], resid[1:]):
    (sx, sy), resp = cv2.phaseCorrelate(a, b, han)
    pc_vecs.append((sx, sy, resp))
pc_dt = time.time() - t0

# keep confident pairs
good = [(sx, sy) for sx, sy, r in pc_vecs if r > 0.03 and math.hypot(sx, sy) > 0.5]
mdx = float(np.median([v[0] for v in good])) if good else 0.0
mdy = float(np.median([v[1] for v in good])) if good else 0.0

def bearing(dx, dy):                       # clockwise from image-up (screen -y)
    return math.degrees(math.atan2(dx, -dy)) % 360
CMP = ["up","up-UR","UR","R-UR","right","R-DR","DR","D-DR","down","D-DL","DL","L-DL","left","L-UL","UL","U-UL"]
def cname(a): return CMP[int((a % 360) / 22.5 + 0.5) % 16]

toward = bearing(mdx, mdy)
speed = math.hypot(mdx, mdy) / MIN_PER_STEP
tow = [bearing(dx, dy) for dx, dy in good]
C = np.mean(np.cos(np.radians(tow))) if tow else 0
S = np.mean(np.sin(np.radians(tow))) if tow else 0
R = math.hypot(C, S)
circ_std = math.degrees(math.sqrt(-2 * math.log(max(R, 1e-6)))) if R > 0 else 999

# 3. downscaled Farneback field on residuals, for the strongest pair
best = max(range(len(pc_vecs)), key=lambda i: pc_vecs[i][2])
SC = 0.25
a = cv2.resize(resid[best], None, fx=SC, fy=SC)
b = cv2.resize(resid[best + 1], None, fx=SC, fy=SC)
fl = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 5, 25, 5, 7, 1.5, 0) / SC
fl = cv2.resize(fl, (W, SKY_H))

# viz: HSV flow map (masked to clouds)
mag, ang = cv2.cartToPolar(fl[..., 0], fl[..., 1])
hsv = np.zeros((SKY_H, W, 3), np.uint8)
hsv[..., 0] = (ang * 90 / np.pi).astype(np.uint8)
hsv[..., 1] = 255
hsv[..., 2] = np.clip(mag * 4, 0, 255).astype(np.uint8)
flow_hsv = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
cm = cmask(resid[best]) & cmask(resid[best + 1])
flow_hsv[~cm] //= 6
cv2.imwrite(os.path.join(out_dir, "flow_hsv.png"), flow_hsv)

# viz: quiver of the dominant per-pair vector arrows on the best frame + big mean arrow
quiver = imgs[best][1].copy()
step = 24
for y in range(0, SKY_H, step):
    for x in range(0, W, step):
        if not cm[y, x]: continue
        dx, dy = fl[y, x]
        if math.hypot(dx, dy) < 1: continue
        cv2.arrowedLine(quiver, (x, y), (int(x + dx), int(y + dy)), (0, 255, 255), 1, tipLength=0.3)
# big mean-drift arrow, centre of sky
cx, cy = W // 2, SKY_H // 2
scale = 120 / max(speed * MIN_PER_STEP, 1e-3)
cv2.arrowedLine(quiver, (cx, cy), (int(cx + mdx / max(abs(mdx),abs(mdy),1e-3)*160),
                int(cy + mdy / max(abs(mdx),abs(mdy),1e-3)*160)), (0, 0, 255), 4, tipLength=0.25)
cv2.imwrite(os.path.join(out_dir, "quiver.png"), quiver)

# composite
bar = np.full((160, W, 3), 28, np.uint8)
lines = [
    f"CLOUD-MOTION -> APPARENT WIND    {len(imgs)} frames / {len(good)} confident pairs, {MIN_PER_STEP:.0f} min apart",
    f"dominant drift: clouds moving TOWARD image-bearing {toward:5.1f} deg  [{cname(toward)}]   (bg-subtracted phase-correlation)",
    f"apparent speed {speed:5.2f} px/min   |   direction consistency R={R:.2f}  (circ-std {circ_std:.0f} deg over the run)",
    f"true compass needs 1-time camera-azimuth calibration (sun/landmark) -- bearing here is IMAGE-relative",
    f"M1 timing: phaseCorrelate {pc_dt*1000/len(pc_vecs):.1f} ms/pair over {len(pc_vecs)} pairs ({W}x{SKY_H} sky ROI)",
]
for i, t in enumerate(lines):
    col = (0,255,255) if i in (1,2) else (200,200,200)
    cv2.putText(bar, t, (12, 26 + i*30), cv2.FONT_HERSHEY_SIMPLEX, 0.56, col, 1, cv2.LINE_AA)
comp = np.vstack([quiver[:SKY_H], flow_hsv, bar])
cv2.imwrite(os.path.join(out_dir, "composite.png"), comp)

res = dict(n_frames=len(imgs), n_pairs=len(pc_vecs), confident_pairs=len(good),
           median_dx=mdx, median_dy=mdy, toward_bearing_deg=toward, toward_label=cname(toward),
           speed_px_per_min=speed, consistency_R=R, circ_std_deg=circ_std,
           ms_per_pair_phasecorr=pc_dt*1000/len(pc_vecs), best_pair_idx=best,
           note="bearing is IMAGE-relative (cw from image-up); true compass needs camera-azimuth calibration")
json.dump(res, open(os.path.join(out_dir, "result.json"), "w"), indent=2)
print(json.dumps(res, indent=2))
