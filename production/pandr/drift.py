#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
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

Baseline re-pinned 2026-07-20 (Elgato Facecam 4K, new aim) from that day's midday
fixed-exposure reference frame. The old-camera baseline history is in TODO.md.

  drift.py --tick             # measure CURRENT sky_ref.jpg vs baseline, append one row
                              # to drift.csv (ts_utc,dx,dy,resp) — called every tick by capture.sh
"""
import cv2, numpy as np, glob, os, sys, datetime

BASELINE = os.path.expanduser("~/monitor/.drift_baseline.jpg")
DRIFT_CSV = os.path.expanduser("~/monitor/drift.csv")

def band(path):
    img = cv2.imread(os.path.expanduser(path), cv2.IMREAD_GRAYSCALE)
    if img is None: return None
    H = img.shape[0]
    return np.float32(img[int(0.52*H):, :])

# --tick: one measurement of the current reference frame, appended to drift.csv.
# Cheap (~0.2 s), safe to run every capture tick; capture.sh warns at |drift| > 40 px.
if len(sys.argv) > 1 and sys.argv[1] == "--tick":
    cur_p = os.path.expanduser("~/monitor/sky_ref.jpg")
    base = band(BASELINE); cur = band(cur_p)
    if base is None or cur is None or cur.shape != base.shape:
        sys.exit("drift --tick: missing baseline or reference frame")
    win = cv2.createHanningWindow((base.shape[1], base.shape[0]), cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(base, cur, win)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    new = not os.path.exists(DRIFT_CSV)
    with open(DRIFT_CSV, "a") as fh:
        if new:
            fh.write("ts_utc,dx,dy,resp\n")
        fh.write(f"{ts},{dx:.2f},{dy:.2f},{resp:.4f}\n")
    print(f"drift {dx:+.2f},{dy:+.2f} px (resp {resp:.3f})")
    sys.exit(0)

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
