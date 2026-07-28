#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""
backfill.py — one-time: push the most recent ~KEEP archived 15-min frames
(~/monitor/archive/DATE/HHMM_{sky,courtyard}.jpg) to the web gallery, so
yesterday-afternoon-through-now is visible without waiting for new ticks.
Renames locally then does ONE scp of the batch (fast), then rebuilds gallery.py.
"""
import glob, os, shutil, subprocess, sys
sys.path.insert(0, os.path.expanduser("~/monitor"))
import gallery

ARCH = os.path.expanduser("~/monitor/archive")
TMP = "/tmp/backfill_recent"

def main():
    frames = {}
    for day in sorted(glob.glob(f"{ARCH}/*")):
        date = os.path.basename(day)
        for f in glob.glob(f"{day}/*_courtyard.jpg"):
            frames.setdefault(f"{date}_{os.path.basename(f)[:4]}Z", {})["courtyard"] = f
        for f in glob.glob(f"{day}/*_sky.jpg"):
            frames.setdefault(f"{date}_{os.path.basename(f)[:4]}Z", {})["sky"] = f
    stamps = sorted(frames)[-gallery.KEEP:]
    shutil.rmtree(TMP, ignore_errors=True); os.makedirs(TMP)
    n = 0
    for s in stamps:
        for cam in ("sky", "courtyard"):
            src = frames[s].get(cam)
            if src and os.path.getsize(src) > 0:
                shutil.copy(src, f"{TMP}/{cam}_{s}.jpg"); n += 1
    print(f"staging {n} files for {len(stamps)} timestamps...")
    gallery.ensure_dir()
    # one batched scp of the whole staging dir
    subprocess.run("scp -q -o BatchMode=yes -i %s %s/*.jpg %s:%s/recent/"
                   % (gallery.KEY, TMP, gallery.HOST, gallery.DIR),
                   shell=True, check=True, timeout=600)
    print(f"gallery rebuilt: {gallery.rebuild()} timestamps")

if __name__ == "__main__":
    main()
