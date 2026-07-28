#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""
aim.py — fast live preview of BOTH cameras, for physically re-aiming them.

  sky (B0578 GS): fast OpenCV grab, auto-exposure — looks fine, ~fast fps.
  courtyard (1080P Low Light): its LIVE OpenCV session can't be tamed (auto
    over-exposes and hunts -> whiteout), so we grab it with imagesnap using a
    fixed low manual exposure + negative brightness offset (a fresh session per
    shot DOES honor manual). ~1 fps but a clean, readable framing view.

Writes small JPEGs to ~/monitor/preview/{sky,courtyard}.jpg; aim.html shows them.
Holds the shared camlock (kept fresh) so the scheduled jobs pause while aiming.
Auto-stops after LIMIT s (default 30 min). Stop early: rmdir ~/monitor/.camlock
or pkill -f aim.py.  Must run under the ssh->localhost hop for camera (TCC) access.
"""
import cv2, numpy as np, os, time, sys, shutil, subprocess, json
sys.path.insert(0, os.path.expanduser("~/monitor"))
import expose

PREV = os.path.expanduser("~/monitor/preview"); os.makedirs(PREV, exist_ok=True)
LOCK = os.path.expanduser("~/monitor/.camlock")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
OUT_W = 900
COURT_EXP = 8                          # starting exposure (predictively auto-adapts below)
COURT_EVERY = 3.0                      # seconds between courtyard imagesnaps (sky stays fast)
SKY_GAMMA = 0.6                        # <1 brightens the (often dim/overcast) sky preview
_SKY_LUT = np.array([min(255, round((i / 255.0) ** SKY_GAMMA * 255)) for i in range(256)], dtype=np.uint8)

for _ in range(8):
    try: os.mkdir(LOCK); break
    except FileExistsError: time.sleep(1)
else:
    shutil.rmtree(LOCK, ignore_errors=True); os.mkdir(LOCK)
open(LOCK + "/owner", "w").write("aim")


def write_prev(name, f):
    h, w = f.shape[:2]
    f = cv2.resize(f, (OUT_W, int(OUT_W * h / w)))
    tmp = f"{PREV}/{name}.tmp.jpg"
    cv2.imwrite(tmp, f, [cv2.IMWRITE_JPEG_QUALITY, 72])
    os.replace(tmp, f"{PREV}/{name}.jpg")            # atomic


def open_sky():
    """(Re)open the sky cam; returns a VideoCapture or None if it's not present."""
    try:
        c = cv2.VideoCapture(expose.find_index((1920, 1200)))
        c.set(cv2.CAP_PROP_FRAME_WIDTH, 1280); c.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        return c
    except Exception:
        return None


sky_cap = None; court = None
try:
    sky_cap = open_sky()
    try:
        court = expose.Arducam(0x0261)          # courtyard cam is optional (often unplugged)
    except Exception as e:
        court = None
        print(f"aim: courtyard cam not found ({e}); previewing SKY only", flush=True)
    print(f"aim: previewing sky (cv2, fast)"
          + (f" + courtyard (imagesnap ~every {COURT_EVERY}s)" if court else " [courtyard absent]")
          + f" for up to {LIMIT}s", flush=True)
    t0 = time.time(); last_court = 0.0; sky_fail = 0; focus_peak = 0.0
    court_exp = COURT_EXP                               # predictively adapt to keep it viewable
    while time.time() - t0 < LIMIT and os.path.isdir(LOCK):
        ok, f = (sky_cap.read() if sky_cap else (False, None))   # sky: fast, every loop
        if ok and f is not None:
            sky_fail = 0
            # focus-assist: sharpness of the raw frame (higher = sharper), with peak-hold
            fv = cv2.Laplacian(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
            focus_peak = max(focus_peak * 0.999, fv)   # peak slowly decays so you can re-baseline
            try:
                with open(f"{PREV}/focus.json.tmp", "w") as fp:
                    fp.write(json.dumps({"sky": round(fv), "peak": round(focus_peak)}))
                os.replace(f"{PREV}/focus.json.tmp", f"{PREV}/focus.json")
            except Exception:
                pass
            write_prev("sky", cv2.LUT(f, _SKY_LUT))   # gamma-brighten for the preview
        else:
            sky_fail += 1
            if sky_fail >= 8:                         # cam unplugged/replugged -> reopen
                try: sky_cap.release()
                except Exception: pass
                sky_cap = open_sky(); sky_fail = 0
                time.sleep(0.5)
        now = time.time()
        if court is not None and now - last_court >= COURT_EVERY:   # courtyard: slower (imagesnap manual)
            try:
                court.manual(); court.set_gain(0); court.set_brightness(0); court.set_exposure(court_exp)
                subprocess.run([expose.IMAGESNAP, "-q", "-w", "0.8", "-d", "Arducam 1080P Low Light",
                                f"{PREV}/courtyard.snap.jpg"], timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                cf = cv2.imread(f"{PREV}/courtyard.snap.jpg")
                if cf is not None:
                    write_prev("courtyard", cf)
                    m = float(cf.mean())              # predictive: brightness ~linear in exposure
                    if not (95 <= m <= 175):          # off target -> jump straight to the right exposure
                        court_exp = int(max(1, min(2000, round(court_exp * 130.0 / max(m, 4)))))
            except Exception:
                pass
            last_court = time.time()
        try: os.utime(LOCK, None)
        except Exception: pass
        time.sleep(0.12)
finally:
    if sky_cap:
        try: sky_cap.release()
        except Exception: pass
    if court:
        try: court.set_brightness(0); court.auto(); court.close()   # restore for other jobs
        except Exception: pass
    shutil.rmtree(LOCK, ignore_errors=True)
    print("aim: stopped, camlock released", flush=True)
