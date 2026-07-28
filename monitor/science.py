#!/usr/bin/env /opt/local/bin/python3.11
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
  campus_hex   average colour of everything BELOW the sky (rows 55-100%) -> campus bar
  ref_exp/ref_gain  camera settings of the reference frame the colours came from
  cloud_frac   fraction of the top 40% that is whitish (low-sat, bright) ~ cloud/haze
  gcc          green chromatic coordinate g/(r+g+b) over a lawn ROI ~ vegetation green-up
  lit_windows  count of window-sized bright blobs on the building band (night-only meaningful)
  + weather pulled from data.json (tempF, humidity, windMph, cloud_cover, text)

Colour swatches (sky_hex, campus_hex) are computed from sky_ref.jpg — the
fixed-settings reference frame (deterministic sun-elevation-keyed exposure, see
expose.py sky_ref) — so the colour-of-the-day bars can't be moved by metering
drift or HDR mode switches (Simon 2026-07-14). Falls back to the metered frame
if the reference is missing/stale. All other metrics stay on the metered frame.

ROIs are fractions of the frame and may need a tweak if the camera is re-aimed.
"""
import cv2, numpy as np, os, sys, json, csv, datetime, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import solar, goldpeak

CSV = os.path.join(HERE, "science.csv")
SKYCOLOR_STATE = os.path.join(HERE, ".skycolor_today.json")
SKYCOLOR_JS = os.path.join(HERE, "skycolor.js")

# ROIs (row fractions; cols where noted) — tuned to the current framing (2026-07-13)
# (average sky colour / hue use goldpeak.sky_region = the "top-middle sixth")
CLOUD_BAND = 0.40               # top 40% for cloud fraction
LAWN_ROWS, LAWN_COLS = (0.66, 0.82), (0.30, 0.95)   # central lawn for greenness
FACADE_ROWS = (0.52, 0.82)      # building façades (window band) for lit-window count
CAMPUS_ROW = 0.55               # rows below this = "campus" (buildings/lawn, no sky)
REF_MAX_AGE = 360               # sky_ref.jpg older than this (s) -> fall back to metered
FIELDS = ["ts_utc", "ts_local", "min_local", "sun_elev", "bright", "sky_hex",
          "sky_hue", "sky_hue_name", "campus_hex", "ref_exp", "ref_gain",
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


def update_skycolor(min_local, date_local, sky_hex, campus_hex):
    try:
        st = json.load(open(SKYCOLOR_STATE))
    except Exception:
        st = {}
    if st.get("date") != date_local:
        st = {"date": date_local, "samples": [], "isamples": []}
    st.setdefault("isamples", [])
    # samples = sky colour (top-middle sixth); isamples = campus colour (below the
    # sky, rows 55-100%; key name kept as "isamples" so the dashboards' JS is
    # unchanged — was whole-image colour before 2026-07-14).
    for key, hexv in (("samples", sky_hex), ("isamples", campus_hex)):
        st[key] = [s for s in st[key] if s[0] != min_local]   # replace same-minute
        st[key].append([min_local, hexv])
        st[key].sort(key=lambda s: s[0])
    json.dump(st, open(SKYCOLOR_STATE, "w"))
    with open(SKYCOLOR_JS, "w") as f:
        f.write("window.SKYCOLOR=" + json.dumps(st, separators=(",", ":")) + ";")


def main():
    # Prefer the standardized (metered single-exposure) frame over the HDR sky.jpg:
    # HDR fusion desaturates the sky, so science metrics/colour read sky_std.jpg.
    std = os.path.join(HERE, "sky_std.jpg")
    sky = sys.argv[1] if len(sys.argv) > 1 else (std if os.path.exists(std) else os.path.join(HERE, "sky.jpg"))
    data = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "data.json")
    img = cv2.imread(sky)
    if img is None:
        print("[science] no sky frame; skipping"); return

    # Fixed-settings reference frame for the colour swatches (see module docstring).
    ref, ref_meta = None, {}
    ref_path = os.path.join(HERE, "sky_ref.jpg")
    try:
        if os.path.exists(ref_path) and time.time() - os.path.getmtime(ref_path) <= REF_MAX_AGE:
            ref = cv2.imread(ref_path)
            ref_meta = json.load(open(os.path.join(HERE, ".sky_ref_meta.json")))
    except Exception:
        ref_meta = {}

    now = datetime.datetime.now(datetime.timezone.utc)
    local = now.astimezone()
    row = {k: "" for k in FIELDS}
    row["ts_utc"] = now.isoformat(timespec="seconds")
    row["ts_local"] = local.isoformat(timespec="seconds")
    row["min_local"] = local.hour * 60 + local.minute
    row["sun_elev"] = round(solar.sun_elevation(now), 2)
    row.update(compute(img, ref))
    if ref is not None:
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

    update_skycolor(row["min_local"], local.strftime("%Y-%m-%d"), row["sky_hex"], row["campus_hex"])
    src = f"ref exp={row['ref_exp']} gain={row['ref_gain']}" if ref is not None else "metered (NO ref)"
    print(f"[science] {row['ts_local']} sun={row['sun_elev']} sky={row['sky_hex']} "
          f"campus={row['campus_hex']} [{src}] cloud%={row['cloud_frac']} gcc={row['gcc']} lit={row['lit_windows']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[science] error: {e}")
