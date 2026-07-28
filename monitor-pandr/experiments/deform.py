#!/usr/bin/env python3
"""Demonstrate (1) cloud tracking and (2) flow deformation (divergence / vorticity)
on one good frame-pair — the substrate for a turbulence diagnostic."""
import cv2, numpy as np, sys, math, os

D = sys.argv[1]; A, B = sys.argv[2], sys.argv[3]; out = sys.argv[4]
a = cv2.imread(f"{D}/{A}_sky.jpg"); b = cv2.imread(f"{D}/{B}_sky.jpg")
H, W = a.shape[:2]; sh = int(0.42 * H)
ga = cv2.cvtColor(a[:sh], cv2.COLOR_BGR2GRAY); gb = cv2.cvtColor(b[:sh], cv2.COLOR_BGR2GRAY)
hsv = cv2.cvtColor(a[:sh], cv2.COLOR_BGR2HSV)
cloud = ((hsv[..., 2] > 150) & (hsv[..., 1] < 70)).astype(np.uint8) * 255

# --- Panel A: sparse LK tracks ---
p0 = cv2.goodFeaturesToTrack(ga, 400, 0.01, 8, mask=cloud)
p1, st, _ = cv2.calcOpticalFlowPyrLK(ga, gb, p0, None, winSize=(31, 31), maxLevel=5)
panelA = a[:sh].copy()
d = []
for (x0, y0), (x1, y1), s in zip(p0.reshape(-1, 2), p1.reshape(-1, 2), st.ravel()):
    if s != 1: continue
    if math.hypot(x1 - x0, y1 - y0) < 2: continue
    cv2.arrowedLine(panelA, (int(x0), int(y0)), (int(x1), int(y1)), (0, 255, 255), 1, tipLength=0.3)
    d.append((x1 - x0, y1 - y0))
d = np.array(d); mdx, mdy = np.median(d, 0)
cv2.putText(panelA, f"{len(d)} cloud tracks  median {math.hypot(mdx,mdy):.0f}px/step",
            (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)

# --- dense flow (Farneback on downscaled residual) for the deformation field ---
bg = np.minimum(ga, gb)  # crude static floor for a single pair
ra = np.clip(ga.astype(np.float32) - bg, 0, 255)
rb = np.clip(gb.astype(np.float32) - bg, 0, 255)
SC = 0.25
fa = cv2.resize(ra, None, fx=SC, fy=SC); fb = cv2.resize(rb, None, fx=SC, fy=SC)
fl = cv2.calcOpticalFlowFarneback(fa, fb, None, 0.5, 5, 25, 5, 7, 1.5, 0)
u, v = fl[..., 0], fl[..., 1]
# spatial gradients -> divergence and vorticity (curl)
dudx = cv2.Sobel(u, cv2.CV_32F, 1, 0, ksize=3); dudy = cv2.Sobel(u, cv2.CV_32F, 0, 1, ksize=3)
dvdx = cv2.Sobel(v, cv2.CV_32F, 1, 0, ksize=3); dvdy = cv2.Sobel(v, cv2.CV_32F, 0, 1, ksize=3)
divg = dudx + dvdy            # >0 spreading apart (cloud growth/updraft), <0 converging
curl = dvdx - dudy            # rotation (shear/eddies)
cmask_s = cv2.resize(cloud, (u.shape[1], u.shape[0])) > 0

def colorize(field, mask, scale, label):
    f = np.clip(field * scale, -1, 1)
    img = np.zeros((*field.shape, 3), np.uint8)
    img[..., 2] = (np.clip(f, 0, 1) * 255).astype(np.uint8)   # red = positive
    img[..., 0] = (np.clip(-f, 0, 1) * 255).astype(np.uint8)  # blue = negative
    img[~mask] //= 8
    img = cv2.resize(img, (W, sh), interpolation=cv2.INTER_NEAREST)
    cv2.putText(img, label, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return img

panelB = colorize(divg, cmask_s, 6, "DIVERGENCE  (red=spreading/growth  blue=converging)")
panelC = colorize(curl, cmask_s, 6, "VORTICITY / curl  (red=CW  blue=CCW  = shear & eddies)")
comp = np.vstack([panelA, panelB, panelC])
cv2.imwrite(out, comp)
print(f"{A}->{B}: {len(d)} tracks, median drift {math.hypot(mdx,mdy):.1f}px  |  "
      f"div rms {np.std(divg[cmask_s]):.3f}  curl rms {np.std(curl[cmask_s]):.3f}")
