#!/usr/bin/env /opt/local/bin/python3.11
"""
experiment.py — weekend A/B harness. Each run captures the SAME scene under
several labeled configurations and publishes a side-by-side comparison page, so
we can look over the results and pick the best settings to run for months.

Which families run depends on the sun (night / golden / day), computed from
solar.py. Frames are archived locally forever (~/monitor/experiments/<family>/)
and the most recent few runs per family are uploaded to
  https://sites.santafe.edu/~simon/experiments.html

Run by launchd (com.lsm.experiment) every 30 min via the ssh->localhost hop.
"""
import cv2, numpy as np, time, os, json, sys, subprocess
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.expanduser("~/monitor"))
import expose, solar, gallery

EXPDIR = os.path.expanduser("~/monitor/experiments")
SUB = "experiments"                 # santafe: ~/html/experiments/
KEEP_RUNS = 4                       # runs kept per family on the web
SKY, COURT = 0x0578, 0x0261


# ---------- condition (night / golden / day) ----------
def condition():
    now = datetime.now(timezone.utc); t = solar.sun_times(now)
    sr, ss = t.get("sunrise"), t.get("sunset")
    if sr and ss and (now > ss + timedelta(minutes=40) or now < sr - timedelta(minutes=40)):
        return "night"
    for a, b in (("golden_morning_start", "golden_morning_end"),
                 ("golden_evening_start", "golden_evening_end")):
        if t.get(a) and t.get(b) and t[a] <= now <= t[b]:
            return "golden"
    return "day"


def fuse(frames, mertens=(1, 1, 1)):
    """Mertens fusion, robust to the odd frame that comes back at the wrong size."""
    frames = [f for f in frames if f is not None]
    h, w = frames[0].shape[:2]
    frames = [f if f.shape[:2] == (h, w) else cv2.resize(f, (w, h)) for f in frames]
    return np.clip(cv2.createMergeMertens(*mertens).process(frames) * 255, 0, 255).astype(np.uint8)


def metrics(f):
    g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
    return {"mean": round(float(g.mean()), 1), "p99": round(float(np.percentile(g, 99)), 1),
            "std": round(float(g.std()), 1)}


# ---------- low-level capture ----------
class SkyCam:
    """Sky cam (0x0578) via OpenCV+libusb. Raw gain write (bypasses the 20 cap)."""
    def __enter__(self):
        self.cam = expose.Arducam(SKY)
        self.idx = expose.find_index((1920, 1200))
        self.cap = cv2.VideoCapture(self.idx); self.cap.set(3, 1920); self.cap.set(4, 1200)
        for _ in range(expose.FLUSH): self.cap.read()
        self.cam.manual(); self.cam.powerline(0)
        return self
    def grab(self, exp, gain=0):
        self.cam.manual(); self.cam._write(4, 2, 2, int(max(0, min(127, gain)))); self.cam.set_exposure(int(exp))
        time.sleep(expose.SETTLE)
        for _ in range(expose.FLUSH): self.cap.read()
        ok, f = self.cap.read(); return f if ok else None
    def hdr(self, target, mults, mertens=(1, 1, 1)):
        E = 8
        for _ in range(6):                          # meter E*
            f = self.grab(E, 0); P, _c = expose.meter(f, expose.CAMERAS["sky"])
            if 0.85*target <= P <= 1.12*target: break
            if P >= expose.SAT_CEIL and E > 1: E = max(1, E//4); continue
            E = int(max(1, min(expose.EXP_MAX, round(E*target/max(P, 1)))))
        exps = sorted({int(max(1, min(expose.EXP_MAX, round(E*m)))) for m in mults})
        frames = [self.grab(e, 0) for e in exps]
        return fuse(frames, mertens), {"E": E, "exps": exps}
    def __exit__(self, *a):
        self.cap.release(); self.cam.close()


def court_snap(exp, gain=0):
    """Courtyard (0x0261): manual via imagesnap (fresh session)."""
    cam = expose.Arducam(COURT)
    cam.manual(); cam._write(4, 2, 2, int(gain)); cam.set_exposure(int(exp)); cam.close()
    time.sleep(0.3)
    expose._snap("Arducam 1080P Low Light", "/tmp/exp_court.jpg")
    f = cv2.imread("/tmp/exp_court.jpg")
    c = expose.Arducam(COURT); c.auto(); c.close()       # leave in auto
    return f

def court_hdr(exps, mertens=(1, 1, 1)):
    cam = expose.Arducam(COURT); frames = []
    for e in exps:
        cam.manual(); cam._write(4, 2, 2, 0); cam.set_exposure(int(e)); time.sleep(0.3)
        expose._snap("Arducam 1080P Low Light", f"/tmp/exp_court_{e}.jpg"); frames.append(cv2.imread(f"/tmp/exp_court_{e}.jpg"))
    cam.auto(); cam.close()
    return fuse(frames, mertens)


# ---------- families: each returns list of (label, frame, meta) ----------
def fam_sky_nightgain():                              # THE night-sky question
    with SkyCam() as s:
        out = []
        for g in (20, 40, 60, 80, 100):
            f = s.grab(5000, g)
            if f is not None: out.append((f"gain{g:03d}", f, {"exp": 5000, "gain": g, **metrics(f)}))
        return out

def fam_sky_hdr():                                    # day/golden sky HDR looks
    with SkyCam() as s:
        out = []
        # Vary target, bracket, and Mertens weights (contrast, saturation,
        # well-exposedness). OpenCV's default well-exposedness weight is 0.0;
        # the current production value of 1.0 pulls toward mid-gray (milkier),
        # so e0 variants test the punchier weighting the docs recommend.
        MID = [1/3, 1, 3, 9, 27]
        for label, tgt, mult, mrt in (
            ("t175_e1",     175, MID, (1, 1, 1)),                    # current production
            ("t175_e0",     175, MID, (1, 1, 0)),                    # OpenCV default, punchier
            ("t175_e0_sat", 175, MID, (1, 1.5, 0)),                  # + saturation
            ("t175_wide",   175, [1/9, 1/3, 1, 3, 9, 27, 81], (1, 1, 0)),
            ("t200_e0",     200, MID, (1, 1, 0)),
            ("t155_e0",     155, MID, (1, 1, 0)),
        ):
            f, info = s.hdr(tgt, mult, mrt); out.append((label, f, {**info, **metrics(f)}))
        return out

def fam_court_day():                                  # day/golden courtyard looks
    out = []
    for e in (1, 2):
        f = court_snap(e); out.append((f"single_exp{e}", f, {"exp": e, **metrics(f)}))
    f = court_hdr([1, 2, 4, 8, 16]); out.append(("hdr_fused", f, metrics(f)))
    return out

def fam_court_night():                                # night courtyard looks
    out = []
    f = court_hdr([2, 8, 30, 120, 500]); out.append(("hdr_longbracket", f, metrics(f)))
    for g in (0, 40, 80):
        f = court_snap(500, g); out.append((f"exp500_gain{g:03d}", f, {"exp": 500, "gain": g, **metrics(f)}))
    return out


FAMILIES = {
    "night":  [("sky_nightgain", fam_sky_nightgain), ("court_night", fam_court_night)],
    "golden": [("sky_hdr", fam_sky_hdr), ("court_day", fam_court_day)],
    "day":    [("sky_hdr", fam_sky_hdr), ("court_day", fam_court_day)],
}


# ---------- publish ----------
def publish(family, stamp, variants):
    ld = f"{EXPDIR}/{family}"; os.makedirs(ld, exist_ok=True)
    rec = {"stamp": stamp, "variants": []}
    files = []
    for label, frame, meta in variants:
        fn = f"{stamp}_{label}.jpg"
        cv2.imwrite(f"{ld}/{fn}", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
        files.append(f"{ld}/{fn}"); rec["variants"].append({"label": label, "file": fn, **meta})
    with open(f"{ld}/log.jsonl", "a") as fp: fp.write(json.dumps(rec) + "\n")
    # upload to santafe experiments/<family>/, prune to newest KEEP_RUNS runs
    gallery.sh_ssh(f"mkdir -p {gallery.DIR}/{SUB}/{family}")
    if files:
        subprocess.run("scp -q -o BatchMode=yes -i %s %s %s:%s/%s/%s/"
                       % (gallery.KEY, " ".join(f"'{x}'" for x in files),
                          gallery.HOST, gallery.DIR, SUB, family), shell=True, timeout=300)
    nvar = max(1, len(variants))
    gallery.sh_ssh(f"cd {gallery.DIR}/{SUB}/{family} && ls -t *.jpg 2>/dev/null | "
                   f"tail -n +{KEEP_RUNS*nvar + 1} | xargs -r rm -f")
    return rec


def build_html(condition_now):
    rows = []
    fams = sorted(set(f for v in FAMILIES.values() for f, _ in v))
    for family in fams:
        listing = gallery.sh_ssh(f"ls -t {gallery.DIR}/{SUB}/{family}/ 2>/dev/null", capture=True)
        names = [n.strip() for n in listing.splitlines() if n.strip().endswith(".jpg")]
        # group by stamp (prefix before first '_LABEL'); stamp = 'YYYY-MM-DD_HHMMZ'
        runs = {}
        for n in names:
            parts = n[:-4].split("_")
            stamp = "_".join(parts[:2]); label = "_".join(parts[2:])
            runs.setdefault(stamp, []).append((label, n))
        if not runs: continue
        rows.append(f"<h2>{family}</h2>")
        for stamp in sorted(runs, reverse=True):
            when = gallery.label(stamp)[0]
            thumbs = "".join(
                f'<figure><img loading="lazy" src="{SUB}/{family}/{n}"><figcaption>{lab}</figcaption></figure>'
                for lab, n in sorted(runs[stamp]))
            rows.append(f'<div class="run"><div class="when">{when} <span>{stamp}</span></div><div class="strip">{thumbs}</div></div>')
    html = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CMU Monitor — experiments</title>
<style>
 body{{margin:0;background:#0b0d12;color:#eef2f8;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}}
 header{{padding:18px 20px;border-bottom:1px solid #232b3d}} h1{{margin:0;font-size:18px}}
 header p{{margin:5px 0 0;color:#8a97b0;font-size:13px}} main{{padding:16px;max-width:1400px;margin:0 auto}}
 h2{{margin:26px 0 6px;font-size:15px;color:#ffb64a;letter-spacing:.03em;border-bottom:1px solid #1c2431;padding-bottom:5px}}
 .run{{margin:10px 0 20px}} .when{{font-size:13px;margin-bottom:6px}} .when span{{color:#5b6577;font-family:ui-monospace,Menlo,monospace;font-size:11px}}
 .strip{{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px}}
 figure{{margin:0;flex:0 0 auto;width:300px}} figure img{{width:300px;border-radius:7px;display:block;background:#000;aspect-ratio:16/10;object-fit:cover}}
 figcaption{{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#9fb0c8;padding:4px 2px}}
</style>
<header><h1>CMU Monitor — weekend experiments</h1>
<p>Same scene, several settings, side by side. Scroll each row horizontally. Newest first. Current condition: <b>{condition_now}</b>. Updates every 30 min.</p></header>
<main>{''.join(rows) if rows else '<p>No experiments captured yet.</p>'}</main>"""
    open("/tmp/experiments.html", "w").write(html)
    gallery.sh_scp("/tmp/experiments.html", f"{gallery.DIR}/experiments.html")


def main():
    cond = condition()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M") + "Z"
    ran = []
    for family, fn in FAMILIES.get(cond, []):
        try:
            variants = fn()
            if variants:
                publish(family, stamp, variants); ran.append(f"{family}({len(variants)})")
        except Exception as e:
            print(f"{family} FAILED: {e}", file=sys.stderr)
    try:
        build_html(cond)
    except Exception as e:
        print(f"build_html FAILED: {e}", file=sys.stderr)
    print(f"[{stamp}] condition={cond} ran: {', '.join(ran) or 'none'}")


if __name__ == "__main__":
    main()
