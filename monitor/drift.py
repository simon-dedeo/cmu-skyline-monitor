#!/usr/bin/env /opt/local/bin/python3.11
"""drift.py — measure camera drift by phase correlation on the building band.

Usage:
  drift.py                    # base = first frame of BASELINE below, targets = all archive_ref days
  drift.py BASE GLOB          # explicit base frame + target glob

Compares each target reference frame (archive_ref, fixed exposure) against the
baseline frame: crops to rows 52-100% (buildings, no sky/clouds), Hanning window,
cv2.phaseCorrelate. dx>0 = content moved right; dy>0 = content moved down.
Response (0-1) is lock confidence — it falls as sun/shadow position diverges from
the baseline frame; treat rows with resp < 0.03 as garbage. Compare like-lit hours
(e.g. same time of day) across days for the cleanest reading.

Baseline set 2026-07-15 ~16:00 EDT after the mount finished settling (23px slip+creep
after that morning's re-aim — see TODO.md / ARCHIVE-2026-07-15/settling-frames).
"""
import cv2, numpy as np, glob, os, sys

BASELINE = os.path.expanduser("~/monitor/.drift_baseline.jpg")

def band(path):
    img = cv2.imread(os.path.expanduser(path), cv2.IMREAD_GRAYSCALE)
    if img is None: return None
    H = img.shape[0]
    return np.float32(img[int(0.52*H):, :])

if len(sys.argv) >= 3:
    base_p, pat = sys.argv[1], sys.argv[2]
else:
    base_p, pat = BASELINE, os.path.expanduser("~/monitor/archive_ref/*/*.jpg")

base = band(base_p)
if base is None:
    sys.exit(f"drift: cannot read base frame {base_p}")
win = cv2.createHanningWindow((base.shape[1], base.shape[0]), cv2.CV_32F)
print(f"# base: {base_p}")
for p in sorted(glob.glob(os.path.expanduser(pat))):
    cur = band(p)
    if cur is None or cur.shape != base.shape: continue
    (dx, dy), resp = cv2.phaseCorrelate(base, cur, win)
    day = os.path.basename(os.path.dirname(p)); t = os.path.basename(p)[:4]
    flag = "  <-- low confidence" if resp < 0.03 else ""
    print(f"{day} {t}Z: dx={dx:+7.2f} dy={dy:+7.2f} (resp {resp:.3f}){flag}")
