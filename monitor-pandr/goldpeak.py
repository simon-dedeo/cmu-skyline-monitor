#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""goldpeak.py — pick the "peak golden hour" frame within a golden-hour window.

Called every capture tick. Given the latest sky frame + data.json (for the solar
golden-hour windows) it:
  * determines whether NOW is inside the morning or evening golden-hour window;
  * picks, inside the PICK_RANGE elevation band, the frame whose sunward sky is
    REDDEST (gold_score >= COLOUR_MIN); on a colourless day it falls back to the
    frame nearest GREY_TARGET[which] (-2 deg for both windows). The old
    golden-blend metric
        blend = saturation * (warmth + 15) * exp(-((bright-110)/55)^2)
    is still computed and reported for science/panels and breaks ties;
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
# --- PICK RULE (Simon 2026-08-18): choose the peak DYNAMICALLY inside an elevation BAND,
#     scored by how red the sky actually is, instead of snapping to one target elevation.
#     Why: a 30-day measurement of the archive (goldcrit.py + ~/Desktop/MBP/golden_analysis/)
#     put the red-sky optimum at elev -3..+3 for BOTH windows, and showed the old -1.0 snap
#     could not tell a spectacular red sunrise from a flat grey one at the same elevation
#     (7 of 30 mornings and 3 of 30 evenings had any red at all).
PICK_RANGE = (-3.0, 3.0)
# GREY-DAY TARGET (Simon 2026-09-16). On the ~70% of mornings with no colour at all the pick
# used to fall to the horizon (elev 0), which gave a flat, washed-out blue-grey frame. Reviewing
# 29 mornings since 08-19 (19 of them grey and all pinned at 0.0): at elev ~-2 the same
# mornings show a deep-blue sky, twilight gradient and lit windows -- much the better frame.
# So colourless frames now rank by proximity to GREY_TARGET[which], not to 0. The same
# review of 26 evenings (contact sheets at +1..-2.5) found the same thing, so evening is -2
# too. BRIGHT_FLOOR still applies, so on a very dark overcast day the pick slides from -2
# toward the horizon until the campus is properly lit (5/19 grey mornings, 4/26 evenings).
GREY_TARGET = {"morning": -2.0, "evening": -2.0}
RULE_ID = "2026-09-16"  # bump when the pick rule changes; stored per index entry so
                        # hours.html can say which rule chose each frame
TARGET_ELEV = dict(GREY_TARGET)                   # exact-position minute: goldtick.sh's
                                                  # --target-time centres the HDR bracket on
                                                  # this crossing. No longer drives the pick;
                                                  # kept defined so importers keep working.
# The glow is LOCALISED, so each window scores one or more sky sub-regions and the colour
# score is the MAX over them (fractions, never a mean: full-width averaging dilutes a vivid
# sunrise to nothing). Camera faces ~north: ENE sunrise glow lands in the RIGHT third.
#   morning: right third only (validated on 29 mornings, 08-19..09-16: all 10 coloured
#            mornings picked correctly).
#   evening (Simon 2026-09-16 review of 26 evenings): sunset colour here is lit cloud
#            UNDERSIDE and it is strongest in the EAST and CENTRE thirds, not the west third
#            near the sun -- 08-27: W 33% / C 50% / E 76%; 09-02: W 0.3% / E 43% (missed
#            entirely by the old west-only region, picked grey at 0.0). So evening = max over
#            all three thirds. Rows stop at 36% (not 42%) because the tall building's roofline
#            enters the centre/right thirds at ~38.5% and, lit by the low pre-sunset sun, read
#            as 3-7% "red" at elev +2.2..+2.9 on every clear evening -- a false colour peak
#            that would drag the pick 20 min before sunset. With the cut, every evening with
#            no visible event scores 0.0 in-band. Fractions: (y0, y1, x0, x1).
SUN_SKY = {"morning": [(0.07, 0.42, 0.667, 0.99)],
           "evening": [(0.07, 0.36, 0.026, 0.339), (0.07, 0.36, 0.339, 0.667),
                       (0.07, 0.36, 0.667, 0.99)]}
# A strongly red sky overrides BRIGHT_FLOOR. The best sunrise in the whole archive
# (2026-08-02 10:28Z, 69% red) has a full-frame mean of 20 -- the floor of 50 would have
# vetoed exactly the frame we most want. This deliberately relaxes the "campus is never a
# silhouette" rule, but ONLY when the sky is unambiguously the subject.
RED_OVERRIDE_PCT = 5.0
BRIGHT_HARD = 12        # below this a frame is unusable no matter how red
COLOUR_MIN = 1.0        # gold_score (%) above which a frame counts as genuinely COLOURED.
                        # Raised from 0.05 on 2026-08-19 after measuring 1,067 in-band archive
                        # frames: among frames with NO real event (red<1%), gold_score still
                        # reaches 0.32 at p99 and 6.1 at worst, so 0.05 let noise-level warmth
                        # promote a grey frame into the coloured tier and drag the pick away
                        # from the horizon. At 1.0 the coloured tier holds 3.6% of in-band
                        # frames vs 31 known real events -- a clean separation. (1.0 is also
                        # the "hit rate" threshold the elevation study was built on.)


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


def _region_colour(img, region):
    """(red_pct, amber_pct, gold_score) for one (y0, y1, x0, x1) fractional sky region."""
    H, W = img.shape[:2]
    y0, y1, x0, x1 = region
    roi = img[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)]
    if roi.size == 0:
        return 0.0, 0.0, 0.0
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hh = hsv[:, :, 0].astype(np.float32) * 2.0            # cv2 hue 0-179 -> degrees
    ss, vv = hsv[:, :, 1], hsv[:, :, 2]
    ok = (ss >= 60) & (vv >= 40) & (vv <= 252)
    red = ok & ((hh <= 25) | (hh >= 335))
    amber = ok & (hh > 25) & (hh <= 50)
    rp, ap = float(red.mean()) * 100.0, float(amber.mean()) * 100.0
    return rp, ap, rp + 0.5 * ap


def sun_sky_stats(img, which):
    """Red / amber pixel FRACTIONS of the sky sub-region(s) where this window's glow appears,
    reporting the region with the highest gold_score (see SUN_SKY). Fractions, never a mean:
    the glow covers only part of the sky, so an average reads blue even during a vivid
    sunrise. Returns (red_pct, amber_pct, gold_score) with red weighted double against
    amber -- amber alone is also ordinary daytime haze."""
    return max((_region_colour(img, r) for r in SUN_SKY[which]), key=lambda t: t[2])


def stats(path, which=None):
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
    out = dict(bright=bright, warm=warm, sat=sat, gfrac=gfrac, glow=glow, blend=blend,
               sky_hue=sky_hue_top(img), img_hue=img_hue_full(img),
               campus_hue=campus_hue_full(img))
    if which in SUN_SKY:
        rp, ap, gs = sun_sky_stats(img, which)
        out.update(red_pct=rp, amber_pct=ap, gold_score=gs)
    return out


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
    # --target-time <data.json>: print the epoch second at which the sun crosses
    # TARGET_ELEV in the active window (bisected to ~1 s), or NONE. No camera.
    if len(sys.argv) > 1 and sys.argv[1] == "--target-time":
        which = current_window(sys.argv[2] if len(sys.argv) > 2 else "data.json")
        if which not in TARGET_ELEV:
            print("NONE"); return
        tgt = TARGET_ELEV[which]
        rising = (which == "morning")
        now = datetime.datetime.now(datetime.timezone.utc)
        prev_t, prev_e = now, solar.sun_elevation(now)
        for m in range(1, 181):
            t2 = now + datetime.timedelta(minutes=m)
            e2 = solar.sun_elevation(t2)
            crossed = (prev_e < tgt <= e2) if rising else (prev_e > tgt >= e2)
            if crossed:
                lo, hi = prev_t, t2
                for _ in range(22):
                    mid = lo + (hi - lo) / 2
                    em = solar.sun_elevation(mid)
                    before = (em < tgt) if rising else (em > tgt)
                    if before:
                        lo = mid
                    else:
                        hi = mid
                print(int(lo.timestamp())); return
            prev_t, prev_e = t2, e2
        print("NONE"); return
    # --window <data.json>: just print the active golden window (or NONE), no camera.
    if len(sys.argv) > 1 and sys.argv[1] == "--window":
        data_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "data.json")
        print(current_window(data_path) or "NONE"); return

    img_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "sky.jpg")
    data_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "data.json")
    which = current_window(data_path)
    if not which:
        print("NONE"); return

    # Outside the pick band there is nothing to choose -- check before decoding the frame.
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    sun_elev_now = solar.sun_elevation(now_utc)
    if not (PICK_RANGE[0] <= sun_elev_now <= PICK_RANGE[1]):
        print("HOLD"); return

    st_img = stats(img_path, which)
    if st_img is None:
        print("HOLD"); return
    # Brightness floor, with the documented red override (see RED_OVERRIDE_PCT).
    if (st_img["bright"] < BRIGHT_HARD
            or (st_img["bright"] < BRIGHT_FLOOR
                and st_img.get("gold_score", 0.0) < RED_OVERRIDE_PCT)):
        print("HOLD"); return

    local = datetime.datetime.now(datetime.timezone.utc).astimezone()
    window_id = f"{local.strftime('%Y-%m-%d')}_{which}"
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    if st.get("window_id") != window_id:
        st = {"window_id": window_id, "best": -1e9}

    # Pick score, lexicographic in two TIERS (magnitudes chosen so tiers cannot overlap:
    # blend maxes out around 1e4, so 1e6/1e12 separations are safe).
    #   COLOURED tier -- any frame with real colour beats every grey frame, and among them
    #     the reddest wins. This is the whole point of the change.
    #   GREY tier -- on the ~75% of days with NO colour at all, rank by proximity to
    #     GREY_TARGET[which] (-2 for both; see the note at GREY_TARGET). Ranking
    #     grey frames by blend alone picked the BRIGHTEST frame, which is always the +3 edge
    #     of the band: on a grey evening that put the display frame ~20 min before sunset.
    #     A fixed grey target keeps a consistent framing when nothing colourful is on offer.
    #     blend breaks ties.
    gs = st_img.get("gold_score", 0.0)
    if gs >= COLOUR_MIN:
        score = 1e12 + gs * 1e6 + st_img["blend"]
    else:
        score = -abs(sun_elev_now - GREY_TARGET[which]) * 1e6 + st_img["blend"]

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
        print("UPLOAD|{}|{}|{}|{}|{}|{}|{}|{}|{:.0f}|{}|{}|{}|{}|{}|{:.2f}|{:.2f}|{:.2f}".format(
            which, tlabel, dlabel, j(sun_elev), j(sky_hue), sname,
            j(exp_ms), j(gain), st_img["blend"], ts, j(img_hue), iname,
            j(campus_hue), cname,
            st_img.get("gold_score", 0.0), st_img.get("red_pct", 0.0),
            st_img.get("amber_pct", 0.0)))
    else:
        print("HOLD")


if __name__ == "__main__":
    main()
