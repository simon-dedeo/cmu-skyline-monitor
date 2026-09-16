#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""goldrepick.py — replay the CURRENT pick rule over the archived golden-window scans and
annotate the index so hours.html can show what the rule now in force would have chosen.

For every (local date, window) since START it:
  * scores each archived HHMM_gold.jpg inside PICK_RANGE exactly as goldpeak.main() does
    (same stats, brightness floor + red override, coloured/grey tiers, GREY_TARGET);
  * rescores the frame the monitor ACTUALLY published (matched by the entry's local time) so
    the existing entry gets gold_score/red_pct/tier under the current scoring;
  * when the rule picks a DIFFERENT frame, writes a downsized copy to repick/
    repick_<date>_<which>.jpg and records a `repick` sub-record on the entry.
Entries at or after goldpeak.RULE_ID are left as-is (they were picked by this rule).
Rewrites .goldindex.json + goldindex.js (via goldindex.write_js). Upload repick/*.jpg and
goldindex.js to santafe afterwards. Run between windows.

  goldrepick.py [START=2026-08-19] [--max-w 1600]
"""
import os, sys, glob, json, datetime, cv2
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE); sys.path.insert(0, HERE)
import solar, goldpeak as gp, goldindex as gi

LOCAL = ZoneInfo("America/New_York")
START = next((a for a in sys.argv[1:] if not a.startswith("--")), "2026-08-19")
MAX_W = int(sys.argv[sys.argv.index("--max-w") + 1]) if "--max-w" in sys.argv else 1600
OUT = os.path.join(HERE, "repick"); os.makedirs(OUT, exist_ok=True)


def frames_by_window():
    """{(local_date, which): [(dt_utc, elev, path)]} for archived golden scans."""
    out = {}
    for day in sorted(glob.glob(os.path.join(HERE, "archive", "2026-*"))):
        d = os.path.basename(day)
        for f in sorted(glob.glob(os.path.join(day, "*_gold.jpg"))):
            hhmm = os.path.basename(f)[:4]
            if not hhmm.isdigit():
                continue
            dt = datetime.datetime(int(d[:4]), int(d[5:7]), int(d[8:10]),
                                   int(hhmm[:2]), int(hhmm[2:]), 30, tzinfo=datetime.timezone.utc)
            el = solar.sun_elevation(dt)
            if not (gp.PICK_RANGE[0] <= el <= gp.PICK_RANGE[1]):
                continue
            loc = dt.astimezone(LOCAL)
            which = "morning" if loc.hour < 12 else "evening"
            ld = loc.date().isoformat()
            if ld < START:
                continue
            out.setdefault((ld, which), []).append((dt, el, f))
    return out


def score(st, el, which):
    """Mirror of goldpeak.main(): None if vetoed, else (score, tier)."""
    gs = st.get("gold_score", 0.0)
    if st["bright"] < gp.BRIGHT_HARD or (st["bright"] < gp.BRIGHT_FLOOR and gs < gp.RED_OVERRIDE_PCT):
        return None
    if gs >= gp.COLOUR_MIN:
        return 1e12 + gs * 1e6 + st["blend"], "coloured"
    return -abs(el - gp.GREY_TARGET[which]) * 1e6 + st["blend"], "grey"


def parse_local(date, tstr):
    try:
        return datetime.datetime.strptime(f"{date} {tstr}", "%Y-%m-%d %I:%M %p").replace(tzinfo=LOCAL)
    except Exception:
        return None


def main():
    items = json.load(open(gi.IDX))
    byw = frames_by_window()
    changed = 0
    for ent in items:
        key = (ent.get("date"), ent.get("which"))
        if key[0] < START or key not in byw:
            continue
        which = key[1]
        scored = []
        for dt, el, f in byw[key]:
            st = gp.stats(f, which)
            if st is None:
                continue
            sc = score(st, el, which)
            scored.append((dt, el, f, st, sc))
        if not scored:
            continue
        # rescore the frame actually published (nearest to the entry's local time)
        t0 = parse_local(ent["date"], ent.get("time", ""))
        if t0:
            dt, el, f, st, sc = min(scored, key=lambda r: abs((r[0] - t0).total_seconds()))
            if abs((dt - t0).total_seconds()) <= 120:
                ent["gold_score"] = round(st.get("gold_score", 0.0), 2)
                ent["red_pct"] = round(st.get("red_pct", 0.0), 2)
                ent["tier"] = sc[1] if sc else "vetoed"
        ent.setdefault("rule", "pre-" + gp.RULE_ID if ent["date"] < gp.RULE_ID else gp.RULE_ID)
        if ent.get("rule") == gp.RULE_ID:
            ent.pop("repick", None)
            continue
        ok = [r for r in scored if r[4] is not None]
        if not ok:
            continue
        dt, el, f, st, sc = max(ok, key=lambda r: r[4][0])
        loc = dt.astimezone(LOCAL)
        same = t0 is not None and abs((dt - t0).total_seconds()) <= 120
        rp = {"time": loc.strftime("%-I:%M %p"), "sun_elev": round(el, 1),
              "gold_score": round(st.get("gold_score", 0.0), 2),
              "red_pct": round(st.get("red_pct", 0.0), 2), "tier": sc[1],
              "changed": not same, "rule": gp.RULE_ID}
        if not same:
            name = f"repick_{ent['date']}_{which}.jpg"
            img = cv2.imread(f)
            h, w = img.shape[:2]
            if w > MAX_W:
                img = cv2.resize(img, (MAX_W, int(round(h * MAX_W / w))), interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(OUT, name), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
            rp["file"] = name
            changed += 1
        ent["repick"] = rp
        print(f"{ent['date']} {which:7s} published {ent.get('time'):>8s} el {ent.get('sun_elev')} "
              f"[{ent.get('tier')}, {ent.get('gold_score')}%] -> rule now: {rp['time']:>8s} "
              f"el {rp['sun_elev']} [{rp['tier']}, {rp['gold_score']}%]{' CHANGED' if not same else ''}")
    json.dump(items, open(gi.IDX, "w"))
    shown = gi.write_js(items)
    print(f"goldrepick: {changed} repicks written to {OUT}; goldindex.js has {len(shown)} entries")


if __name__ == "__main__":
    main()
