#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""science.py — Tier-0 science logger for the CMU Skyline Monitor.

Called once per capture tick (from capture.sh) AFTER update.py has written a fresh
sky.jpg + data.json. Computes a compact set of per-frame metrics and appends one row
to science.csv (tiny — ~kilobytes/day; never uploaded). Also maintains skycolor.js:
today's 5-minute average-sky-colour samples, which the dashboard renders as a
colour-of-the-day bar. Fails soft — a bad frame never breaks the tick.

Metrics (see header row):
  sun_elev     sun elevation above horizon (deg), solar.sun_elevation
  bright       full-frame mean brightness (0-255)
  sky_hex      average colour of the top-middle-sixth sky region -> the sky bar colour
  sky_hue      saturation-weighted mean hue of that band (deg) + sky_hue_name
  campus_hex   average colour of everything BELOW the sky (rows 48-100%) -> campus bar
  ref_exp/ref_gain  camera settings of the reference frame the colours came from
  cloud_frac   fraction of the top 40% that is whitish (low-sat, bright) ~ cloud/haze
  gcc          green chromatic coordinate g/(r+g+b) over a lawn ROI ~ vegetation green-up
  lit_windows  count of window-sized bright blobs on the building band (night-only meaningful)
  + weather pulled from data.json (tempF, humidity, windMph, cloud_cover, text)

ALL metrics + colour swatches read sky_ref.jpg — the FIXED-exposure reference frame
(skyshot.sh: sun-elevation-keyed exposure + gain, fixed daylight WB) — so brightness
and colour are comparable across time and can't drift with metering. The displayed
sky.jpg is HDR-fused (tone-mapped, not radiometric), so science falls back to it only
if the reference is missing/stale.

ROIs are fractions of the frame and may need a tweak if the camera is re-aimed.
"""
import cv2, numpy as np, os, sys, json, csv, datetime, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import solar, goldpeak

CSV = os.path.join(HERE, "science.csv")
SKYCOLOR_STATE = os.path.join(HERE, ".skycolor_today.json")
SKYCOLOR_HIST = os.path.join(HERE, ".skycolor_hist.json")   # last 7 completed days of colour series
SKYCOLOR_JS = os.path.join(HERE, "skycolor.js")

# ROIs (row fractions; cols where noted) — tuned to the Elgato Facecam 4K framing
# (2026-07-20): blue sky in the top ~35%, two building façades mid-frame, manicured
# lawn in the lower third. (Average sky colour / hue use goldpeak.sky_region.)
CLOUD_BAND = 0.35               # top 35% = clear sky above all rooftops, for cloud fraction
LAWN_ROWS, LAWN_COLS = (0.824, 0.88), (0.51, 0.61)  # SMALL pure-grass patch, lower-middle third
                                                     # (top edge -20px per Simon 2026-07-20)
                                                     # -> average grass colour + greenness (gcc)
FACADE_ROWS = (0.46, 0.71)      # both buildings' window band, for lit-window count
CAMPUS_ROW = 0.48               # rows below this = "campus" (buildings/lawn, no sky)
REF_MAX_AGE = 360               # sky_ref.jpg older than this (s) -> fall back to metered
FIELDS = ["ts_utc", "ts_local", "min_local", "sun_elev", "bright", "sky_hex",
          "sky_hue", "sky_hue_name", "campus_hex", "grass_hex", "ref_exp", "ref_gain",
          "cloud_frac", "gcc", "lit_windows",
          "tempF", "humidity", "windMph", "cloud_cover", "weather_text"]


def avg_bgr(region):
    return [float(region[:, :, i].mean()) for i in range(3)]


def compute(img, ref=None):
    H, W = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    m = {}
    m["bright"] = round(float(gray.mean()), 1)

    # Colour swatches come from the fixed-settings reference frame when available
    # (metering/HDR can't move them); everything else uses the metered frame.
    src = img if ref is None else ref
    # OKLab-perceptual average colour of the "top-middle sixth" (goldpeak.sky_region) + hue
    m["sky_hex"] = goldpeak.avg_hex(goldpeak.sky_region(src))
    m["sky_hue"] = goldpeak.sky_hue_top(src)
    m["sky_hue_name"] = goldpeak.hue_name(m["sky_hue"])
    # "campus" = everything below the skyline (buildings, lawns, roofs — no sky)
    m["campus_hex"] = goldpeak.avg_hex(src[int(CAMPUS_ROW * src.shape[0]):])

    # cloud fraction: whitish (low-sat, bright) pixels in the top 40%
    top = cv2.cvtColor(img[:int(CLOUD_BAND * H)], cv2.COLOR_BGR2HSV)
    m["cloud_frac"] = round(float(((top[:, :, 1] < 40) & (top[:, :, 2] > 110)).mean()) * 100, 2)

    # greenness (GCC) over the lawn ROI
    lawn = img[int(LAWN_ROWS[0] * H):int(LAWN_ROWS[1] * H),
               int(LAWN_COLS[0] * W):int(LAWN_COLS[1] * W)]
    lb, lg, lr = avg_bgr(lawn)
    m["gcc"] = round(lg / (lr + lg + lb + 1e-6), 4)
    m["grass_hex"] = goldpeak.avg_hex(lawn)   # average grass colour (small pure-lawn ROI)

    # lit-window index: window-sized bright blobs on the façade band (night-meaningful)
    fac = gray[int(FACADE_ROWS[0] * H):int(FACADE_ROWS[1] * H), :]
    _, th = cv2.threshold(fac, 190, 255, cv2.THRESH_BINARY)
    n, _, stats, _ = cv2.connectedComponentsWithStats(th, 8)
    lit = 0
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if 4 <= area <= 3000 and w <= 120 and h <= 120:
            lit += 1
    m["lit_windows"] = lit
    return m


def update_skycolor(min_local, date_local, sky_hex, campus_hex, grass_hex):
    try:
        st = json.load(open(SKYCOLOR_STATE))
    except Exception:
        st = {}
    try:
        hist = json.load(open(SKYCOLOR_HIST))
    except Exception:
        hist = {"days": []}
    if st.get("date") != date_local:
        # Day rolled over: archive the completed day into the 7-day history (the dashboards
        # show it when hovering a colour bar), then start today fresh.
        if st.get("date") and st.get("samples"):
            hist["days"] = [d for d in hist["days"] if d.get("date") != st["date"]]
            hist["days"].append({"date": st["date"], "samples": st.get("samples", []),
                                 "isamples": st.get("isamples", []), "gsamples": st.get("gsamples", [])})
            hist["days"] = sorted(hist["days"], key=lambda d: d["date"])[-7:]
            json.dump(hist, open(SKYCOLOR_HIST, "w"))
        st = {"date": date_local, "samples": [], "isamples": [], "gsamples": []}
    st.setdefault("isamples", []); st.setdefault("gsamples", [])
    # samples = sky colour (top-middle sixth); isamples = campus colour (below the sky,
    # rows 48-100%; key kept for dashboard-JS compat); gsamples = grass colour (small lawn ROI).
    for key, hexv in (("samples", sky_hex), ("isamples", campus_hex), ("gsamples", grass_hex)):
        st[key] = [s for s in st[key] if s[0] != min_local]   # replace same-minute
        st[key].append([min_local, hexv])
        st[key].sort(key=lambda s: s[0])
    json.dump(st, open(SKYCOLOR_STATE, "w"))
    out = dict(st)
    out["days"] = hist["days"]                                # last 7 completed days
    with open(SKYCOLOR_JS, "w") as f:
        f.write("window.SKYCOLOR=" + json.dumps(out, separators=(",", ":")) + ";")


def main():
    # Primary frame = the FIXED-exposure reference (sky_ref.jpg + its .meta, from
    # skyshot.sh). Fixed exposure/gain/WB per sun elevation => brightness + colour are
    # comparable across time. Fall back to the HDR display frame (sky.jpg) only if the
    # reference is missing/stale (it's tone-mapped, so metrics then aren't radiometric).
    ref_png = os.path.join(HERE, "sky_ref.png")     # lossless (skyshot.sh) — preferred input
    ref_jpg = os.path.join(HERE, "sky_ref.jpg")     # saved JPEG (fallback if PNG already cleaned)
    ref_meta, use_ref = {}, False
    def _fresh(p):
        return os.path.exists(p) and time.time() - os.path.getmtime(p) <= REF_MAX_AGE
    if len(sys.argv) > 1:
        sky = sys.argv[1]
    elif _fresh(ref_png):
        sky, use_ref = ref_png, True
    elif _fresh(ref_jpg):
        sky, use_ref = ref_jpg, True
    else:
        sky = os.path.join(HERE, "sky.jpg")
    if use_ref:
        try:
            ref_meta = json.load(open(ref_jpg + ".meta"))
        except Exception:
            ref_meta = {}
    data = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "data.json")
    img = cv2.imread(sky)
    if img is None:
        print("[science] no sky frame; skipping"); return
    try:
        import flatfield; img = flatfield.apply(img)   # remove fixed lens/glass spots before metrics
    except Exception:
        pass

    now = datetime.datetime.now(datetime.timezone.utc)
    local = now.astimezone()
    row = {k: "" for k in FIELDS}
    row["ts_utc"] = now.isoformat(timespec="seconds")
    row["ts_local"] = local.isoformat(timespec="seconds")
    row["min_local"] = local.hour * 60 + local.minute
    row["sun_elev"] = round(solar.sun_elevation(now), 2)
    row.update(compute(img))
    if use_ref:
        row["ref_exp"] = ref_meta.get("exposure", "")
        row["ref_gain"] = ref_meta.get("gain", "")
    # lit-window count is only meaningful after dark; blank it in daylight (bright
    # façades otherwise produce meaningless bright fragments).
    if row["sun_elev"] > 2:
        row["lit_windows"] = ""

    try:
        c = json.load(open(data)).get("weather", {}).get("current", {})
        row["tempF"] = c.get("tempF", "")
        row["humidity"] = c.get("humidity", "")
        row["windMph"] = c.get("windMph", "")
        row["cloud_cover"] = c.get("cloud", "")
        row["weather_text"] = c.get("text", "")
    except Exception:
        pass

    new = not os.path.exists(CSV)
    with open(CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)

    update_skycolor(row["min_local"], local.strftime("%Y-%m-%d"), row["sky_hex"], row["campus_hex"], row["grass_hex"])
    src = f"ref exp={row['ref_exp']} gain={row['ref_gain']}" if use_ref else "sky.jpg HDR (NO ref)"
    print(f"[science] {row['ts_local']} sun={row['sun_elev']} sky={row['sky_hex']} "
          f"campus={row['campus_hex']} grass={row['grass_hex']} [{src}] cloud%={row['cloud_frac']} gcc={row['gcc']} lit={row['lit_windows']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[science] error: {e}")
