#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""goldindex.py — maintain the accumulating index of golden-hour PEAK frames that
feeds the "recent hours" page (hours.html). Upserts one (date, which) entry into
.goldindex.json and rewrites goldindex.js (window.GOLDINDEX = [...] sorted date asc,
morning before evening). Each entry points at peak_<date>_<which>.jpg on santafe.

  goldindex.py <date> <which> <time> <sun_elev> <sky_hue> <sky_hue_name>
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.join(HERE, ".goldindex.json")
JS = os.path.join(HERE, "goldindex.js")


def num(v):
    try:
        return float(v)
    except Exception:
        return None


def main():
    date, which, time, sun_elev, sky_hue, sname = (sys.argv[1:7] + [""] * 6)[:6]
    try:
        items = json.load(open(IDX))
    except Exception:
        items = []
    items = [x for x in items if (x.get("date"), x.get("which")) != (date, which)]
    items.append({"date": date, "which": which, "time": time,
                  "sun_elev": num(sun_elev), "sky_hue": num(sky_hue),
                  "sky_hue_name": sname, "file": f"peak_{date}_{which}.jpg"})
    items.sort(key=lambda x: (x["date"], 0 if x["which"] == "morning" else 1))
    json.dump(items, open(IDX, "w"))
    with open(JS, "w") as f:
        f.write("window.GOLDINDEX=" + json.dumps(items, separators=(",", ":")) + ";")
    print(f"goldindex: {len(items)} entries")


if __name__ == "__main__":
    main()
