#!/usr/bin/env /opt/local/bin/python3.11
"""
gallery.py — publish timestamped hourly frames + a browsable gallery to the web,
so overnight frames can be reviewed WITHOUT logging into the MacBook.

Called by timelapse.sh each hour:  gallery.py <stampZ> <sky.jpg> <courtyard.jpg>

It uploads the two frames to  ~/html/recent/{sky,courtyard}_<stampZ>.jpg on santafe,
prunes to the most recent KEEP per camera, and regenerates ~/html/gallery.html
(newest first, both cameras per row, labelled in Pittsburgh local time).
Public at https://sites.santafe.edu/~simon/gallery.html
"""
import subprocess, sys, os
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    PGH = ZoneInfo("America/New_York")
except Exception:
    PGH = None

KEY = os.path.expanduser("~/.ssh/id_rsa")
HOST = "simon@santafe.santafe.edu"
DIR = "/home/simon/html"
KEEP = 96                       # per camera (~24 h at the 15-min cadence)

def sh_scp(src, dst):
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=25",
                    "-i", KEY, src, f"{HOST}:{dst}"], check=True, timeout=60)

def sh_ssh(cmd, capture=False):
    a = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=25", "-i", KEY, HOST, cmd]
    if capture:
        return subprocess.run(a, capture_output=True, text=True, timeout=60).stdout
    subprocess.run(a, check=True, timeout=60)

def label(stamp):
    # stamp like 2026-07-10_0300Z
    try:
        dt = datetime.strptime(stamp, "%Y-%m-%d_%H%MZ").replace(tzinfo=timezone.utc)
        loc = dt.astimezone(PGH) if PGH else dt
        return loc.strftime("%a %-I:%M %p").upper(), loc.strftime("%b %-d")
    except Exception:
        return stamp, ""

def ensure_dir():
    sh_ssh(f"mkdir -p {DIR}/recent")

def publish_pair(stamp, sky, court):
    for cam, f in (("sky", sky), ("courtyard", court)):
        if os.path.exists(f) and os.path.getsize(f) > 0:
            sh_scp(f, f"{DIR}/recent/{cam}_{stamp}.jpg")

def rebuild():
    # prune to newest KEEP per camera
    sh_ssh(f"cd {DIR}/recent && for c in sky courtyard; do "
           f"ls -t ${{c}}_*.jpg 2>/dev/null | tail -n +{KEEP+1} | xargs -r rm -f; done")
    # list what's there now, derive unique stamps (newest first)
    names = [n.strip() for n in sh_ssh(f"ls -t {DIR}/recent/ 2>/dev/null", capture=True).splitlines() if n.strip().endswith(".jpg")]
    stamps = []
    for n in names:
        s = n.replace("sky_", "").replace("courtyard_", "").replace(".jpg", "")
        if s not in stamps:
            stamps.append(s)
    # build gallery
    rows = []
    for s in stamps:
        when, day = label(s)
        rows.append(f"""<figure><figcaption><b>{when}</b> <span>{day} · {s}</span></figcaption>
<div class="pair"><img loading="lazy" src="recent/sky_{s}.jpg" alt="sky {s}">
<img loading="lazy" src="recent/courtyard_{s}.jpg" alt="courtyard {s}"></div></figure>""")
    html = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CMU Monitor — recent frames</title>
<style>
 body{{margin:0;background:#0b0d12;color:#eef2f8;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}}
 header{{padding:20px 22px;border-bottom:1px solid #232b3d}}
 h1{{margin:0;font-size:19px}} header p{{margin:5px 0 0;color:#8a97b0;font-size:13px}}
 main{{max-width:1100px;margin:0 auto;padding:18px}}
 figure{{margin:0 0 26px}}
 figcaption{{display:flex;justify-content:space-between;align-items:baseline;padding:0 2px 7px;border-bottom:1px solid #1c2431;margin-bottom:8px}}
 figcaption b{{font-size:16px;letter-spacing:.02em}} figcaption span{{color:#5b6577;font-size:12px;font-family:ui-monospace,Menlo,monospace}}
 .pair{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
 .pair img{{width:100%;border-radius:9px;background:#000;display:block;aspect-ratio:16/9;object-fit:contain}}
 @media(max-width:640px){{.pair{{grid-template-columns:1fr}}}}
</style>
<header><h1>CMU Courtyard Monitor — recent hourly frames</h1>
<p>Skyline (left) &amp; Courtyard (right), newest first · times in Pittsburgh local · updates every 15 min · last ~24 h</p></header>
<main>{''.join(rows) if rows else '<p>No frames yet.</p>'}</main>"""
    tmp = "/tmp/gallery.html"
    open(tmp, "w").write(html)
    sh_scp(tmp, f"{DIR}/gallery.html")
    return len(stamps)

def main():
    stamp, sky, court = sys.argv[1], sys.argv[2], sys.argv[3]
    ensure_dir()
    publish_pair(stamp, sky, court)
    print(f"gallery: {rebuild()} timestamps published")

if __name__ == "__main__":
    main()
