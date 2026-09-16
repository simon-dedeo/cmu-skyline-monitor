#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""goldindex.py — maintain the accumulating index of golden-hour PEAK frames that
feeds the "recent hours" page (hours.html). Upserts one (date, which) entry into
.goldindex.json and rewrites goldindex.js (window.GOLDINDEX = [...] sorted date asc,
morning before evening). Each entry points at peak_<date>_<which>.jpg on santafe.

  goldindex.py <date> <which> <time> <sun_elev> <sky_hue> <sky_hue_name> [gold_score red_pct rule]

The three optional trailing fields (added 2026-09-16) record WHY the frame was picked:
gold_score/red_pct from goldpeak's colour score, and the goldpeak.RULE_ID in force. hours.html
shows them as a "coloured N% red" / "grey -> -2 deg" badge. Entries may also carry a `repick`
sub-record written by goldrepick.py (what the current rule would have chosen on a day that
was picked under an older rule); this script preserves any such existing fields on upsert.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.join(HERE, ".goldindex.json")
JS = os.path.join(HERE, "goldindex.js")

# Only surface peaks from this date onward on hours.html (Simon 2026-08-19). Everything
# before it was chosen by the OLD nearest-to-target-elevation rule, so it is not
# representative of what the monitor now picks. The FULL history is still kept in
# .goldindex.json -- this only filters what goldindex.js publishes, so reverting is a
# one-line change and nothing is lost. Set to "" to show everything again.
INDEX_START = "2026-08-19"


def write_js(items):
    """Rewrite goldindex.js from the stored index, applying INDEX_START."""
    shown = [x for x in items if str(x.get("date", "")) >= INDEX_START]
    with open(JS, "w") as f:
        f.write("window.GOLDINDEX=" + json.dumps(shown, separators=(",", ":")) + ";")
    return shown


def num(v):
    try:
        return float(v)
    except Exception:
        return None


def main():
    try:
        items = json.load(open(IDX))
    except Exception:
        items = []
    # --rebuild: regenerate goldindex.js from the stored index without adding an entry
    # (used to apply a changed INDEX_START immediately instead of waiting for a window).
    if len(sys.argv) > 1 and sys.argv[1] == "--rebuild":
        shown = write_js(items)
        print(f"goldindex: rebuilt, {len(shown)} of {len(items)} entries shown "
              f"(INDEX_START={INDEX_START or 'none'})")
        return
    date, which, time, sun_elev, sky_hue, sname, gold_score, red_pct, rule = \
        (sys.argv[1:10] + [""] * 9)[:9]
    prev = next((x for x in items if (x.get("date"), x.get("which")) == (date, which)), {})
    items = [x for x in items if (x.get("date"), x.get("which")) != (date, which)]
    ent = dict(prev)                       # keep repick/backfill fields if present
    ent.update({"date": date, "which": which, "time": time,
                "sun_elev": num(sun_elev), "sky_hue": num(sky_hue),
                "sky_hue_name": sname, "file": f"peak_{date}_{which}.jpg",
                "thumb": f"thumb_{date}_{which}.jpg"})   # 1600-px copy made by the publishers
    if gold_score != "":
        ent["gold_score"] = num(gold_score)
    if red_pct != "":
        ent["red_pct"] = num(red_pct)
    if rule != "":
        ent["rule"] = rule
    items.append(ent)
    items.sort(key=lambda x: (x["date"], 0 if x["which"] == "morning" else 1))
    json.dump(items, open(IDX, "w"))          # full history, unfiltered
    shown = write_js(items)                   # published subset
    print(f"goldindex: {len(items)} entries, {len(shown)} shown "
          f"(INDEX_START={INDEX_START or 'none'})")


if __name__ == "__main__":
    main()
