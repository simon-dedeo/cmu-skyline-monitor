#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""goldpeak.py — pick the "peak golden hour" frame within a golden-hour window.

Called every capture tick. Given the latest sky frame + data.json (for the solar
golden-hour windows) it:
  * determines whether NOW is inside the morning or evening golden-hour window;
  * picks the frame captured NEAREST sun elevation -1 deg (1 deg below the
    horizon), morning and evening alike (Simon 2026-07-16, chosen after
    reviewing the 7/15-16 review panels: the -1 deg point is the right balance
    for both windows). Elevation is monotonic within a window, so the running
    -max logic peaks exactly once. The old golden-blend metric
        blend = saturation * (warmth + 15) * exp(-((bright-110)/55)^2)
    is still computed and reported for science/panels — it just no longer
    drives the pick (its evening drift past sunset was structural; see the
    7/16 panels artifact);
  * keeps a per-window running peak in .golden_peak.json;
  * prints 'UPLOAD|which|H:MM AM/PM|YYYY-MM-DD|blend|golden%|horizon%|bright|sat|warm'
    when the frame is a new peak (capture.sh uploads it + writes golden.js), else
    'HOLD', or 'NONE' when outside any golden window.

Also reports golden-hue % (warm 15-50 deg, saturated sky pixels) and horizon-gold %
for the caption. A brightness floor rejects near-black frames. Delete
.golden_peak.json to reset the current window.
"""
import cv2, numpy as np, json, os, sys, datetime, math

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import solar                                   # sun_elevation() for the caption
STATE = os.path.join(HERE, ".golden_peak.json")
ROI_TOP = 0.62          # top 62% (sky + upper façades) for the golden warm/sat/blend stats
BRIGHT_FLOOR = 50       # full-frame mean; reject near-black twilight frames
TARGET_ELEV = {"morning": -1.0, "evening": -1.0}   # both windows, from the golden-hour row review 2026-07-26 (was morning 3.46 / evening -0.63)


def hue_name(deg):
    """Coarse colour name for a hue angle in degrees (0-360)."""
    if deg is None:
        return "—"
    d = deg % 360
    for lo, hi, name in [(0, 15, "red"), (15, 45, "amber"), (45, 70, "gold"),
                         (70, 160, "green"), (160, 200, "cyan"), (200, 255, "blue"),
                         (255, 290, "violet"), (290, 345, "magenta"), (345, 360, "red")]:
        if lo <= d < hi:
            return name
    return "—"


# Sky sample = central-upper open sky (Elgato 4K framing, 2026-07-20): rows 5-35%
# (clear sky above the rooflines), central third of the columns. Avoids corner
# skyglare and keeps out the building façades. Used for the golden hue + colour bar.
SKY_ROWS, SKY_COLS = (0.05, 0.35), (1.0 / 3, 2.0 / 3)


def sky_region(img):
    H, W = img.shape[:2]
    return img[int(SKY_ROWS[0] * H):int(SKY_ROWS[1] * H),
               int(SKY_COLS[0] * W):int(SKY_COLS[1] * W)]


# --- Perceptual colour averaging in OKLab. Averaging gamma-encoded sRGB bytes is
#     physically wrong (dark/muddy bias); averaging in OKLab (a perceptually-uniform
#     space) gives the perceptual mean colour. Pipeline: sRGB -> linear -> OKLab,
#     average L,a,b, then OKLab -> linear -> sRGB. Used for the colour SWATCHES and the
#     displayed hues. (GCC stays a raw-sRGB channel ratio = the phenocam convention;
#     the blend peak-selection score's warm/sat/bright stay on sRGB DN = tuned scoring.)
def _srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _mean_oklab(region):
    """Mean OKLab (L, a, b) over a BGR uint8 region."""
    a = region.reshape(-1, 3).astype(np.float64) / 255.0             # BGR in 0..1
    bl, gl, rl = _srgb_to_linear(a[:, 0]), _srgb_to_linear(a[:, 1]), _srgb_to_linear(a[:, 2])
    l = 0.4122214708 * rl + 0.5363325363 * gl + 0.0514459929 * bl
    m = 0.2119034982 * rl + 0.6806995451 * gl + 0.1073969566 * bl
    s = 0.0883024619 * rl + 0.2817188376 * gl + 0.6299787005 * bl
    l_, m_, s_ = np.cbrt(l), np.cbrt(m), np.cbrt(s)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    A = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return float(L.mean()), float(A.mean()), float(B.mean())


def _mean_srgb(region):
    """OKLab-mean colour of a BGR region -> (r, g, b) sRGB in 0..1."""
    L, A, B = _mean_oklab(region)
    l_ = L + 0.3963377774 * A + 0.2158037573 * B
    m_ = L - 0.1055613458 * A - 0.0638541728 * B
    s_ = L - 0.0894841775 * A - 1.2914855480 * B
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    enc = lambda c: (lambda x: x * 12.92 if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055)(min(1.0, max(0.0, c)))
    return enc(r), enc(g), enc(b)


def avg_hex(region):
    """Perceptual (OKLab) average colour of a BGR region -> sRGB hex string."""
    r, g, b = _mean_srgb(region)
    return "#%02x%02x%02x" % (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def _hue(region):
    """HSV hue (deg 0-360) of the OKLab-average colour — consistent with avg_hex, and
    naturally chroma-weighted (grey pixels don't shift the mean a/b direction). Kept in
    the HSV convention so the colour names (hue_name) stay intuitive."""
    r, g, b = _mean_srgb(region)
    mx, mn = max(r, g, b), min(r, g, b); d = mx - mn
    if d < 1e-9:
        return 0.0
    if mx == r:   h = ((g - b) / d) % 6
    elif mx == g: h = (b - r) / d + 2
    else:         h = (r - g) / d + 4
    return round((h * 60) % 360, 1)


def sky_hue_top(img):
    """Hue of the top-middle sixth (open sky)."""
    return _hue(sky_region(img))


def img_hue_full(img):
    """Hue of the WHOLE frame (sky + buildings + lawn) — overall scene colour."""
    return _hue(img)


CAMPUS_ROW = 0.48   # rows below this = "campus" (buildings/lawn, no sky) — keep in
                    # step with science.py CAMPUS_ROW; re-tune if the cam is re-aimed


def campus_hue_full(img):
    """Hue of everything BELOW the skyline (rows 48-100%) — campus colour.
    Replaced whole-image hue in the golden caption (Simon 2026-07-14)."""
    return _hue(img[int(CAMPUS_ROW * img.shape[0]):])


def cam_stats():
    """Exposure (ms) and gain the sky cam last used, from state_sky.json (written by
    expose.py). Returns (exp_ms, gain), either possibly None."""
    try:
        st = json.load(open(os.path.join(HERE, "state_sky.json")))
        e = st.get("exposure")
        return (round(e / 10.0, 2) if e is not None else None, st.get("gain"))
    except Exception:
        return (None, None)


def parse(v):
    if not v:
        return None
    dt = datetime.datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def stats(path):
    img = cv2.imread(path)
    if img is None:
        return None
    bright = float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean())
    H = img.shape[0]
    roi = img[:int(ROI_TOP * H)]
    b, g, r = [float(roi[:, :, i].mean()) for i in range(3)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hh, ss, vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    sat = float(ss.mean())
    warm = r - b
    gmask = (hh >= 8) & (hh <= 25) & (ss >= 60) & (vv >= 40) & (vv <= 250)
    gfrac = float(gmask.mean()) * 100
    lo = roi[int(roi.shape[0] * 0.66):]
    lh = cv2.cvtColor(lo, cv2.COLOR_BGR2HSV)
    glow = float(((lh[:, :, 0] >= 8) & (lh[:, :, 0] <= 25) &
                  (lh[:, :, 1] >= 60) & (lh[:, :, 2] >= 40) & (lh[:, :, 2] <= 250)).mean()) * 100
    blend = sat * (warm + 15) * math.exp(-((bright - 110) / 55) ** 2)
    return dict(bright=bright, warm=warm, sat=sat, gfrac=gfrac, glow=glow, blend=blend,
                sky_hue=sky_hue_top(img), img_hue=img_hue_full(img),
                campus_hue=campus_hue_full(img))


def current_window(data_path):
    """Return 'morning'/'evening' if NOW is inside a solar golden-hour window, else
    None. Camera-free — used by goldtick.sh to decide whether to scan this minute."""
    try:
        s = json.load(open(data_path))["solar"]
    except Exception:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    for w, a, b in (("morning", "golden_morning_start", "golden_morning_end"),
                    ("evening", "golden_evening_start", "golden_evening_end")):
        A, B = parse(s.get(a)), parse(s.get(b))
        if A and B and A <= now <= B:
            return w
    return None


def main():
    # --window <data.json>: just print the active golden window (or NONE), no camera.
    if len(sys.argv) > 1 and sys.argv[1] == "--window":
        data_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "data.json")
        print(current_window(data_path) or "NONE"); return

    img_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "sky.jpg")
    data_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "data.json")
    which = current_window(data_path)
    if not which:
        print("NONE"); return

    st_img = stats(img_path)
    if st_img is None or st_img["bright"] < BRIGHT_FLOOR:
        print("HOLD"); return

    local = datetime.datetime.now(datetime.timezone.utc).astimezone()
    window_id = f"{local.strftime('%Y-%m-%d')}_{which}"
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    if st.get("window_id") != window_id:
        st = {"window_id": window_id, "best": -1e9}

    # Pick score = negative distance from the TARGET_ELEV choice point; the frame
    # closest to TARGET_ELEV wins the window. blend is still in the stats below.
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    sun_elev_now = solar.sun_elevation(now_utc)
    score = -abs(sun_elev_now - TARGET_ELEV[which])

    if score > st.get("best", -1e9):
        tlabel, dlabel = local.strftime("%-I:%M %p"), local.strftime("%Y-%m-%d")
        ts = now_utc.isoformat(timespec="seconds")           # for the "system up" light
        # caption fields: sun elevation (top-middle-sixth) sky hue, camera exposure/gain
        sun_elev = round(sun_elev_now, 1)
        sky_hue = st_img.get("sky_hue")
        sname = hue_name(sky_hue)
        img_hue = st_img.get("img_hue")
        iname = hue_name(img_hue)
        campus_hue = st_img.get("campus_hue")
        cname = hue_name(campus_hue)
        exp_ms, gain = cam_stats()
        st.update(best=score, which=which, time=tlabel, date=dlabel, ts=ts,
                  sun_elev=sun_elev, sky_hue=sky_hue, sky_hue_name=sname,
                  img_hue=img_hue, img_hue_name=iname,
                  campus_hue=campus_hue, campus_hue_name=cname, exp_ms=exp_ms, gain=gain,
                  **{k: round(v, 2) for k, v in st_img.items()
                     if k not in ("sky_hue", "img_hue", "campus_hue")})
        json.dump(st, open(STATE, "w"))
        j = lambda v: "null" if v is None else v
        # NB: goldscan.sh parses this line POSITIONALLY (fields 2 & 10) — only
        # ever APPEND new fields.
        print("UPLOAD|{}|{}|{}|{}|{}|{}|{}|{}|{:.0f}|{}|{}|{}|{}|{}".format(
            which, tlabel, dlabel, j(sun_elev), j(sky_hue), sname,
            j(exp_ms), j(gain), st_img["blend"], ts, j(img_hue), iname,
            j(campus_hue), cname))
    else:
        print("HOLD")


if __name__ == "__main__":
    main()
