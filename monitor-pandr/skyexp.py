#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""skyexp.py — sun-elevation -> Elgato Facecam 4K exposure plan (reference + HDR bracket).

The Elgato's UVC `exposure-time-abs` is in 0.1 ms units, range 1..1000 (0.1 ms .. 100 ms);
`gain` is 0..160. Two products:

  reference : a FIXED per-elevation (exposure, gain) so frames at the same sun elevation
              are radiometrically comparable — a cloudy noon then reads DARKER than a
              clear noon, which is real science signal (this is the point of a fixed
              reference vs auto-exposure). Feeds science.py colour swatches + the
              fixed-exposure timelapse.
  bracket   : a geometric exposure spread (+/- 2 stops) around the reference, for a
              Mertens HDR fuse -> the pretty DISPLAY frame (sky highlights kept AND
              shadow/building detail opened).

Table seeded 2026-07-20 from a daytime calibration on pandr (clear sky, elev ~40 deg:
E=8 -> mean 98, E=16 -> mean 140 @ 0.1% clip, E=32 -> 21% clip). Daytime rows are
measured; the dawn/dusk/night rows are PHYSICS-SEEDED GUESSES (exposure roughly doubles
per ~6 deg of elevation lost near the horizon, then gain takes over) and should be
refined from science.csv `ref_exp`/`bright`/`sky_hex` over real twilight — see TODO_NEW.
"""
import sys

CLAMP = (1, 1000)          # exposure-time-abs units (0.1 ms); 1 = 0.1 ms, 1000 = 100 ms
GAIN_CLAMP = (0, 160)

# (sun_elev_deg, exposure_0.1ms, gain) — descending elevation. Interpolated linearly.
TABLE = [
    ( 60,    8,   0),      # high summer sun, clear  (measured)
    ( 40,   12,   0),      # mid sun                 (measured ~mean 120)
    ( 25,   20,   0),      # low sun                 (measured region)
    ( 15,   40,   0),      # golden-ish              (measured region)
    (  6,   90,   0),      # near sunset             (seed)
    (  0,  180,   0),      # horizon                 (seed)
    ( -3,  360,   0),      # civil twilight          (seed)
    ( -6,  700,  20),      # deep twilight           (seed)
    (-10, 1000,  60),      # night onset             (seed)
    (-90, 1000, 160),      # deep night (still dark) (seed)
]

STOPS = [-2, 0, 2]         # HDR bracket, in stops, around the reference exposure (3 frames,
                           # 16:1 range — enough DR for a fused display frame; 3 SkyCam
                           # launches keeps the tick ~14 s instead of ~27 s)


def _clamp(v, lo, hi):
    return int(max(lo, min(hi, round(v))))


def plan(sun_elev):
    t = TABLE
    if sun_elev >= t[0][0]:
        e, g = t[0][1], t[0][2]
    elif sun_elev <= t[-1][0]:
        e, g = t[-1][1], t[-1][2]
    else:
        e = g = None
        for (a_el, a_e, a_g), (b_el, b_e, b_g) in zip(t, t[1:]):
            if b_el <= sun_elev <= a_el:
                f = (sun_elev - b_el) / (a_el - b_el)      # 0 at lower anchor, 1 at upper
                e = b_e + f * (a_e - b_e)
                g = b_g + f * (a_g - b_g)
                break
    e = _clamp(e, *CLAMP)
    g = _clamp(g, *GAIN_CLAMP)
    bracket = sorted({_clamp(e * (2.0 ** s), *CLAMP) for s in STOPS})
    return {"exposure": e, "gain": g, "bracket": bracket}


if __name__ == "__main__":
    elev = float(sys.argv[2]) if len(sys.argv) > 2 else float(sys.argv[1])
    p = plan(elev)
    if len(sys.argv) > 1 and sys.argv[1] == "--sh":
        # shell-eval'able: EXPOSURE=.. GAIN=.. BRACKET="a b c .."
        print(f'EXPOSURE={p["exposure"]} GAIN={p["gain"]} BRACKET="{" ".join(map(str, p["bracket"]))}"')
    else:
        print(p)
