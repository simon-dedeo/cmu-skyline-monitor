#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""spotmap.py — nightly PER-PIXEL spot map from the accumulated crops (SPOT.md §4/§5).

Spots are NOT assumed circular or uniform: for every pixel in the 448x448 spot crop we
median-stack the ratio linearized(pixel)/quadratic-annulus-background across the last N
gated frames, per channel. The result is an empirical transmittance-like map T(x,y,c)
that captures arbitrary shape and unevenness (window dots/blemishes convolved with the
pupil footprint). Outputs per night: spot_lab/fits/<date>/spotmap_{b,g,r}.npy + a viz PNG
(deviation from 1.0, amplified). Runs entirely on the Air; ~1-2 min for 300 crops.
"""
import cv2, numpy as np, glob, os, datetime, json
HOME = os.path.expanduser("~/monitor")
CROPS = os.path.join(HOME, "spot_lab", "crops")
OUTD = os.path.join(HOME, "spot_lab", "fits", datetime.datetime.now().strftime("%Y%m%d"))
N_MAX = 300
R_ANN0, R_ANN1 = 110, 160


def _crop_centre():
    """The spot's LOCAL coordinates inside the 448 crop. spotlab clamps the crop origin
    to the frame (y0 = max(0, cy-224)), so a spot nearer the top edge than 224 px is NOT
    at the crop centre -- assuming (224,224) put the background annulus half on the spot
    (review 2026-07-28, finding 8). Derive from the tracked model, fall back to centre."""
    try:
        m = json.load(open(os.path.join(HOME, "spot_model.json")))
        s0 = m["spots"][0]
        fx, fy = float(s0["fx"]) * 3840, float(s0["fy"]) * 2160
        x0, y0 = max(0.0, fx - 224), max(0.0, fy - 224)
        lx, ly = fx - x0, fy - y0
        if 40 < lx < 408 and 40 < ly < 408:
            return lx, ly
    except Exception:
        pass
    return 224.0, 224.0


CX, CY = _crop_centre()


def srgb_lin(v):
    v = v / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def main():
    fs = sorted(glob.glob(os.path.join(CROPS, "*", "*_spot.png")))[-N_MAX:]
    if len(fs) < 20:
        print(f"[spotmap] only {len(fs)} crops; skipping"); return
    os.makedirs(OUTD, exist_ok=True)
    yy, xx = np.mgrid[0:448, 0:448]
    rr = np.sqrt((xx - CX) ** 2 + (yy - CY) ** 2)
    ann = (rr >= R_ANN0) & (rr < R_ANN1)
    ay, ax = np.where(ann)
    A = np.column_stack([np.ones_like(ax), ax - CX, ay - CY,
                         (ax - CX) ** 2, (ax - CX) * (ay - CY), (ay - CY) ** 2]).astype(np.float64)
    E = np.column_stack([np.ones(448 * 448), (xx - CX).ravel(), (yy - CY).ravel(),
                         ((xx - CX) ** 2).ravel(), ((xx - CX) * (yy - CY)).ravel(),
                         ((yy - CY) ** 2).ravel()]).astype(np.float64)
    stacks = {c: [] for c in range(3)}
    for f in fs:
        im = cv2.imread(f)
        if im is None or im.shape[:2] != (448, 448):
            continue
        for c in range(3):
            ch = srgb_lin(im[:, :, c].astype(np.float64))
            av = ch[ann]
            med = np.median(av)
            if med < 0.005 or (im[:, :, c] > 250).mean() > 0.005:   # too dark / clipped
                continue
            coef, *_ = np.linalg.lstsq(A, av, rcond=None)
            bg = (E @ coef).reshape(448, 448)
            stacks[c].append(ch / np.maximum(bg, 1e-6))
    names = "bgr"
    used = 0
    for c in range(3):
        if len(stacks[c]) < 15:
            continue
        T = np.median(np.stack(stacks[c]), axis=0).astype(np.float32)
        used = max(used, len(stacks[c]))
        np.save(os.path.join(OUTD, f"spotmap_{names[c]}.npy"), T)
        viz = np.clip((1.0 - T) * 12.0, -1, 1)          # dip amplified 12x, signed
        img = np.zeros((448, 448, 3), np.uint8)
        img[..., 2] = np.clip(viz, 0, 1) * 255           # red = dimming
        img[..., 0] = np.clip(-viz, 0, 1) * 255          # blue = brightening
        cv2.imwrite(os.path.join(OUTD, f"spotmap_{names[c]}.png"), img)
    print(f"[spotmap] built from up to {used} frames -> {OUTD}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[spotmap] error: {e}")
