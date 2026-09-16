#!/usr/bin/env python3
"""Downscale archived 4K sky frames to 1080p for timelapse compilation.

Reads ~/monitor/archive/<YYYY-MM-DD>/<HHMM>_sky.jpg (the 5-min HDR-fused served
frames; _gold.jpg golden-hour scans are excluded to keep cadence uniform) and
writes ~/monitor/tl_stage/<YYYY-MM-DD>_<HHMM>.jpg at 1920x1080. Resumable:
frames already staged are skipped. Runs niced so it stays out of the way of the
5-min capture ticks.
"""
import os, sys, glob, cv2
from concurrent.futures import ProcessPoolExecutor

BASE = os.path.expanduser("~/monitor")
OUT  = os.path.join(BASE, "tl_stage")
W, H = 1920, 1080

def jobs():
    out = []
    for src in sorted(glob.glob(os.path.join(BASE, "archive", "20*", "[0-9]*_sky.jpg"))):
        day  = os.path.basename(os.path.dirname(src))
        hhmm = os.path.basename(src).split("_")[0]
        dst  = os.path.join(OUT, "%s_%s.jpg" % (day, hhmm))
        if not os.path.exists(dst):
            out.append((src, dst))
    return out

def one(job):
    src, dst = job
    img = cv2.imread(src)
    if img is None:
        return src                                  # unreadable -> report, skip
    small = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
    tmp = dst + ".tmp.jpg"
    cv2.imwrite(tmp, small, [cv2.IMWRITE_JPEG_QUALITY, 92])
    os.replace(tmp, dst)
    return None

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    todo = jobs()
    print("todo %d" % len(todo), flush=True)
    bad, done = [], 0
    with ProcessPoolExecutor(max_workers=4) as ex:
        for r in ex.map(one, todo, chunksize=8):
            done += 1
            if r: bad.append(r)
            if done % 200 == 0:
                print("%d/%d" % (done, len(todo)), flush=True)
    print("done %d, unreadable %d" % (done, len(bad)), flush=True)
    for b in bad: print("BAD %s" % b, flush=True)
