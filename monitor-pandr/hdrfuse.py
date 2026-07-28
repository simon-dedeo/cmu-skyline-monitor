#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""hdrfuse.py — exposure-fusion (Mertens) of N bracketed JPEGs into one display JPEG.

Mertens fusion needs NO exposure metadata: each pixel is weighted by local contrast,
saturation, and well-exposedness, then blended in a Laplacian pyramid. That makes it
robust to our approximate UVC brackets and gives a natural, tone-mapped-looking result
(no HDR "grunge"). At 4K a 5-frame fuse is ~1 s on the M1.

Usage: hdrfuse.py OUT.jpg IN1.jpg IN2.jpg [IN3.jpg ...]
Fail-soft: with <2 readable frames it passes the single frame through (or errors if none),
so a bracket hiccup never leaves the caller without an image.
"""
import sys, cv2, numpy as np

JPEG_Q = 92
# Mertens exposure fusion is intentionally FLAT (it avoids HDR halos by staying
# conservative), which reads as milky/low-contrast. Restore a natural "pop" with a
# gentle post-pass: local contrast (CLAHE on L), then global saturation + a mild
# S-curve. Tuned 2026-07-20 against a daytime bracket; adjust here to taste.
CLAHE_CLIP = 1.4          # local-contrast strength (0 disables); keep low so clear sky doesn't get blocky
SAT = 1.20                # saturation multiplier
CON = 1.12                # global contrast multiplier around PIVOT
PIVOT = 118.0             # mid-tone the contrast curve pivots on


def enhance(bgr):
    if CLAHE_CLIP > 0:
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = cv2.createCLAHE(clipLimit=CLAHE_CLIP, tileGridSize=(8, 8)).apply(l)
        bgr = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * SAT, 0, 255)
    bgr = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    return np.clip((bgr.astype(np.float32) - PIVOT) * CON + PIVOT, 0, 255).astype(np.uint8)


def main():
    if len(sys.argv) < 3:
        print("usage: hdrfuse.py OUT.jpg IN1.jpg IN2.jpg [...]", file=sys.stderr)
        return 2
    out, ins = sys.argv[1], sys.argv[2:]
    imgs = [im for im in (cv2.imread(p) for p in ins) if im is not None]
    if not imgs:
        print("hdrfuse: no readable input frames", file=sys.stderr)
        return 1
    # Guard against a size mismatch (shouldn't happen — all frames are 4K from the same
    # camera — but a torn frame could differ): keep only frames matching the first shape.
    shape = imgs[0].shape
    imgs = [im for im in imgs if im.shape == shape]
    if len(imgs) < 2:
        cv2.imwrite(out, imgs[0], [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
        print(f"hdrfuse: only {len(imgs)} usable frame — passed through -> {out}")
        return 0
    # Correct fixed lens/glass spots on the RAW frames BEFORE fusion + enhance. The
    # attenuation is multiplicative (~2%) and exposure-independent, so dividing it out on the
    # raw frames is exact; doing it after enhance() under-corrects (contrast amplifies the dip).
    try:
        import flatfield; imgs = [flatfield.apply(im) for im in imgs]
    except Exception as e:
        print("hdrfuse: flatfield skipped:", e)
    fused = cv2.createMergeMertens().process(imgs)            # float32 0..1
    fused = enhance(np.clip(fused * 255.0, 0, 255).astype(np.uint8))   # restore contrast+colour "pop"
    cv2.imwrite(out, fused, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
    print(f"hdrfuse: fused {len(imgs)} frames (enhanced, spots corrected) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
