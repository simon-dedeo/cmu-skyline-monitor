#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""goldpromote.py — make the CURRENT rule's picks the displayed golden-hour frames.

One-off follow-up to goldrepick.py (Simon 2026-09-16: "current rule is good; go with that").
For every index entry carrying a `repick` with changed=true, the archived frame the current
rule chose becomes the entry: full-res copy staged as promote/peak_<date>_<which>.jpg (same
name as the published file, so it REPLACES the old frame on santafe/ganesha), time/elev/score/
tier/sky hue updated, rule set to goldpeak.RULE_ID, and the old choice kept in
`repicked_from` so the page can say so. Also writes a 1600-px thumb_<date>_<which>.jpg for
EVERY entry (hours.html used to load 4K JPEGs as thumbnails) and records it as `thumb`.
Then rewrites .goldindex.json + goldindex.js. Upload promote/* + goldindex.js afterwards.
"""
import os, sys, json, glob, datetime, cv2
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import goldpeak as gp, goldindex as gi
LOCAL = ZoneInfo("America/New_York"); OUT = os.path.join(HERE, "promote"); os.makedirs(OUT, exist_ok=True)
THUMB_W = 1600


def frame_for(date, tstr):
    """Archived HHMM_gold.jpg nearest this local date+time (within 2 min), or None."""
    try:
        t = datetime.datetime.strptime(f"{date} {tstr}", "%Y-%m-%d %I:%M %p").replace(tzinfo=LOCAL)
    except Exception:
        return None
    u = t.astimezone(datetime.timezone.utc)
    best = None
    for dd in (u.date(), u.date() + datetime.timedelta(days=1), u.date() - datetime.timedelta(days=1)):
        for f in glob.glob(os.path.join(HERE, "archive", dd.isoformat(), "*_gold.jpg")):
            hhmm = os.path.basename(f)[:4]
            if not hhmm.isdigit():
                continue
            ft = datetime.datetime(dd.year, dd.month, dd.day, int(hhmm[:2]), int(hhmm[2:]), 30, tzinfo=datetime.timezone.utc)
            d = abs((ft - u).total_seconds())
            if d <= 120 and (best is None or d < best[0]):
                best = (d, f)
    return best[1] if best else None


def thumb(src, name):
    img = cv2.imread(src)
    if img is None:
        return False
    h, w = img.shape[:2]
    if w > THUMB_W:
        img = cv2.resize(img, (THUMB_W, int(round(h * THUMB_W / w))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(os.path.join(OUT, name), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return True


items = json.load(open(gi.IDX)); promoted = thumbs = 0
for e in items:
    if str(e.get("date", "")) < gi.INDEX_START:
        continue
    rp = e.get("repick")
    if rp and rp.get("changed"):
        src = frame_for(e["date"], rp["time"])
        if src is None:
            print("!! no archive frame for repick", e["date"], e["which"], rp["time"]); continue
        st = gp.stats(src, e["which"]) or {}
        e["repicked_from"] = {"time": e.get("time"), "sun_elev": e.get("sun_elev"), "rule": e.get("rule")}
        e.update(time=rp["time"], sun_elev=rp["sun_elev"], gold_score=rp["gold_score"],
                 red_pct=rp["red_pct"], tier=rp["tier"], rule=gp.RULE_ID,
                 sky_hue=st.get("sky_hue"), sky_hue_name=gp.hue_name(st.get("sky_hue")))
        import shutil; shutil.copy2(src, os.path.join(OUT, e["file"]))
        promoted += 1
        print(f"promoted {e['date']} {e['which']}: {e['repicked_from']['time']} -> {e['time']} (el {e['sun_elev']}, {e['tier']} {e['gold_score']}%)")
    else:
        e["rule"] = gp.RULE_ID          # rule agreed with this frame
    e.pop("repick", None)
    src = os.path.join(OUT, e["file"]) if os.path.exists(os.path.join(OUT, e["file"])) else frame_for(e["date"], e.get("time", ""))
    tname = f"thumb_{e['date']}_{e['which']}.jpg"
    if src and thumb(src, tname):
        e["thumb"] = tname; thumbs += 1
    else:
        print("!! no thumb source for", e["date"], e["which"])
json.dump(items, open(gi.IDX, "w")); shown = gi.write_js(items)
print(f"goldpromote: {promoted} promoted, {thumbs} thumbs, {len(shown)} entries in goldindex.js")
