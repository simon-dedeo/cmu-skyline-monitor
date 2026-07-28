# CMU Skyline Monitor — golden-hour system & 2026-07-13 changes

This documents the golden-hour "peak" pipeline and the other changes made on
2026-07-13. Companion to `REPORT.md` (original build) and `README.md`.

## What the public site shows now

`https://proofsandreasons.io/dashboard.html` (hotlinks `sites.santafe.edu/~simon/`):
- **Featured photo = the most recent inferred _peak_ golden-hour frame** (`sky.jpg`),
  captioned **"Most recent inferred peak golden hour — <morning|evening> · <time> ·
  today|yesterday · latest golden hour"** plus a data line:
  **inferred peak · sun position (elevation) · avg sky hue (top-middle sixth) · avg
  total image hue (whole frame)** — the last from `goldpeak.img_hue_full`. Held between
  windows. The homepage (`index.html`) shows the same image + caption below the akdeniz
  usage panel.
  A footer **"system up"** light is green when the featured image is < 20 h old
  (from `golden.js` ts). Load is shown as **% of total CPUs**. The "recent hours"
  footer link opens **hours.html** (see below). No more "TIMELAPSE DAY N" line.
  (Peak is still ranked by the golden-blend score internally; the data line just
  reports different, more science-y fields.)
- Live **weather** panels (data.json) still refresh ~every 15 min.
- The tilted **courtyard** cam is not shown (and is currently unplugged).

`today|yesterday` is computed in the page from the capture date vs. the **viewer's**
local clock, so it stays correct all day/night.

## The "peak golden" criterion (golden-blend)

Chosen by Simon 2026-07-13 after reviewing real windows (see
`/Users/simon/Desktop/MBP/golden_candidates/`). We score each in-window sky frame:

```
warm   = meanR - meanB        # over the sky ROI (top 62%)
sat    = mean HSV saturation  # over the sky ROI
bright = full-frame mean gray
blend  = sat * (warm + 15) * exp(-((bright - 110) / 55)^2)
```

`blend` rewards a vivid, warm, well-exposed sky and down-weights both washed-out
midday and dark twilight. It lands on the raking-golden-light frame (Jul 13 ~6:31 AM,
Jul 12 ~8:22 PM) rather than the dramatic-but-black pre-dawn frame that raw
**saturation** picks (saturation is fooled by deep pre-dawn *blue* — the 5:44 AM
frame scored sat 55 but only 0.9% golden-hue, vs the real 6:00–6:31 AM golden).

Also reported for the caption (not used for ranking): **golden-hue %** = share of sky
pixels with hue 15–50° (cv2 HSV 8–25), saturation ≥ 60, value 40–250; and
**horizon-gold %** = same over the lower third of the sky ROI. A brightness floor
(full-frame mean ≥ 50) rejects near-black frames so the campus is never a silhouette.

To retune: edit `goldpeak.py` (`stats()` for the metric, `BRIGHT_FLOOR`, the hue band).

## Cadence: 1-minute scan during golden hours, 5-minute otherwise

Golden hours are short, so to actually hit the peak we scan **every 1 minute** during
the solar golden-hour windows, and keep the normal **5-minute** cadence the rest of
the day. Extra 1-min frames are deleted once the window ends — we keep only the 5-min
archive plus each window's single saved peak.

- **`com.lsm.monitor`** (StartInterval 300, 5 min) → `tick.sh` → `capture.sh`:
  captures both cams, writes `data.json`/`data.js`, **archives fresh frames** to
  `archive/YYYY-MM-DD/HHMM_*.jpg`, grabs the night-boost variant when dark, and
  uploads **weather only** (~every 15 min, throttled by `.last_upload`). It no longer
  uploads the courtyard frame or the gallery, and no longer handles golden.
- **`com.lsm.goldscan`** (StartInterval 60, 1 min) → `goldtick.sh`:
  - camera-free check `goldpeak.py --window data.json`;
  - **inside a window** it hops `ssh localhost` → `goldscan.sh`, which grabs a fresh sky
    frame, scores it with `goldpeak.py`, and when it beats the window's running peak
    (tracked in `.golden_peak.json`) saves it **LOCALLY only** (`.goldpeak_frame.jpg` +
    `archive/YYYY-MM-DD/peak_<which>.jpg`). Nothing is uploaded during the window;
  - **when the window closes** (first out-of-window tick with a saved peak) it uploads
    the final peak to santafe **ONCE**: `sky.jpg` + dated `peak_<date>_<which>.jpg` +
    `golden.js` + `goldindex.js`, all in a single `scp -C` connection, then cleans up.
- Both jobs share the **camlock** (`camlock.sh`), so they never collide on the camera.

Capture takes ~15–28 s (HDR sky fuse), so 1-minute cadence is safe; faster (<~45 s)
would risk overlapping captures. `goldscan` skips a minute if the 5-min tick holds the
camlock.

`golden.js` (on santafe) is:
`window.GOLDEN={which,time,date,ts,sun_elev,sky_hue,sky_hue_name,img_hue,img_hue_name,exp_ms,gain,blend}`
(ts = capture UTC ISO for the system-up light; sun_elev deg via `solar.sun_elevation`;
sky_hue = saturation-weighted circular-mean hue of the **top-middle sixth**
`goldpeak.sky_region` = rows 0-50%, cols ⅓-⅔ — central upper sky, dodges corner
skyglare/reflections; exp_ms/gain from `state_sky.json`).

**Recent hours page** (`hours.html` on santafe): at window close the dated
`peak_<date>_<which>.jpg` + `goldindex.js` are uploaded (via `goldindex.py`).
`hours.html` reads `goldindex.js` and shows the morning/evening peak **pairs** by date,
newest first — they accumulate over the run.

## Upload policy to santafe (minimal — audited 2026-07-13)

Uploads are batched into **one `scp -C` connection each** (compression on; text shrinks
~3×). rsync is not usable (santafe has no rsync binary).

1. **Weather + colour** (`data.json` ~7 KB, `data.js` ~5 KB, `skycolor.js` 1–4 KB) — one
   compressed connection every ~15 min (96/day). Disk/CPU/load ride along in data.json,
   so they refresh on the dashboard every ~15 min, in step with the colour bar.
2. **Golden peak** (`sky.jpg` + dated peak ~300 KB each + `golden.js` + `goldindex.js`) —
   one connection at each window **close**, i.e. **twice a day**.

Rough totals: **~98 connections/day, ~1.5–2 MB/day** (text compressed; the two JPEG
pairs dominate volume). Trivial for santafe. Everything is archived locally at 5-min
(and single frames too) regardless — nothing else is uploaded (no courtyard, no gallery,
no per-minute golden churn).

## Night-boost pilot (archive-only, never uploaded)

When the sky frame is dark (mean < 60), `capture.sh` also grabs a gain-boosted variant
via `expose.py sky_night` (fixed exposure 5000 + gain 60, single frame) →
`archive/DAY/HHMM_sky_night.jpg`. Goal: pull the blue-hour gradient / city glow the
flicker-free exposure leaves black, for a nicer dusk/dawn movie. **Deep night is
genuinely just noise** even at gain 100 (measured), so this only helps at twilight.

## Bad-frame fix (2026-07-11 06:39 dawn ghost/banding)

`expose.py fuse()` used to **resize** off-resolution frames before Mertens fusion. The
GS sky cam occasionally delivers 1920×1080 instead of 1200; stretching + fusing that
produced a ghosted, horizontally-banded frame at the dawn/dusk transition. Now `fuse()`
**drops** off-resolution frames (keeps the modal size), and `capture_manual` filters
bracket frames to the expected resolution and keeps the single metered frame if fewer
than 2 remain.

## Cameras / focus

- **Sky — Arducam B0578 GS (0x0c45:0x0578):** manual UVC exposure works; used for
  everything. **No focus control at all** — all UVC focus requests STALL, i.e. it is a
  **fixed/manual-focus M12 lens**. There is no autofocus to set to infinity; focus is a
  physical lens ring. Since the whole scene is the distant skyline, **max sharpness =
  infinity focus** — set it with the aimer's focus-assist readout.
- **Courtyard — Arducam 1080P Low Light (0x0c45:0x0261):** on a tilt and **currently
  unplugged**. `capture.sh` skips archiving its stale frame; `aim.py` treats it as
  optional.

## Aimer

`aim.py` (run via `ssh localhost`, holds the camlock so scheduled jobs pause) previews
the sky fast with a **focus-assist** sharpness number (`preview/focus.json`, peak-held);
courtyard is now **optional** (previews sky-only if it's absent). View at
`http://127.0.0.1:8787/aim.html` on the Mac (`open` it). Start/stop:

```bash
# start (60-min limit), detached, camera via ssh->localhost:
ssh laboratoryforsocialminds@laboratorys-mbp.wifi.local.cmu.edu \
  'ssh -i ~/.ssh/id_ed25519 localhost "cd ~/monitor && nohup /opt/local/bin/python3.11 aim.py 3600 </dev/null >>aim.log 2>&1 &"'
# stop:
ssh …laboratorys-mbp… 'pkill -f aim.py; rm -rf ~/monitor/.camlock'
```

## launchd jobs (2026-07-13)

Active: **com.lsm.monitor** (5-min), **com.lsm.goldscan** (1-min), **com.lsm.monitor-web**
(localhost:8787 for the dashboard/aim pages).
Disabled (moved to `~/Library/LaunchAgents/disabled-by-claude/`, reversible): **com.lsm.experiment**
(night-gain question answered) and **com.lsm.timelapse** (redundant with the 5-min archive).

Re-enable a disabled job:
```bash
mv ~/Library/LaunchAgents/disabled-by-claude/com.lsm.timelapse.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.lsm.timelapse.plist
```

## Operator cheat-sheet

```bash
launchctl list | grep lsm                                   # job status
tail -f ~/monitor/monitor.log                               # monitor + goldscan logs
launchctl kickstart -k gui/$(id -u)/com.lsm.monitor         # force a 5-min tick
rm ~/monitor/.golden_peak.json                              # reset the current golden window's peak
rm ~/monitor/.last_upload                                   # force a weather upload next tick
cat ~/monitor/golden.js                                     # what the site is currently flagging
```

## Sky exposure — adaptive HDR + standardized frame (2026-07-13)

The sky cam used to HDR-fuse a bracket on every daytime tick. Measured finding: HDR
fusion **desaturates the sky** (sky-ROI saturation 13% fused vs 21% on the single
metered frame, with the buildings *equally* bright midday) — it only earns its keep in
**high-contrast** scenes (dusk/overcast, buildings sunk in shadow), which is why it was
added (REPORT.md §6b). So:

- **Adaptive HDR** (`expose.py capture_manual`): meter the single exposure E\*, then fuse
  the bracket **only if** the single frame's building band (rows 52–82%) mean is below
  `hdr_building_dark` (default **90** — buildings in shadow). Otherwise keep the crisper,
  more-saturated single frame. Mirrors the courtyard cam's adaptive logic. Threshold is a
  tunable in `CAMERAS["sky"]`.
- **Standardized frame** (`sky_std.jpg`): expose.py always saves the metered single
  frame (constant-target, not tone-mapped) alongside the adaptive `sky.jpg`. **science.py
  + the colour bar read `sky_std.jpg`** (truer, consistent colour). capture.sh also
  **archives it** each tick (`archive/DAY/HHMM_sky_std.jpg`) so single-vs-adaptive can be
  reviewed at end of week.
- **Note:** single (non-HDR) daytime frames were NOT saved before 2026-07-13 ~11:19; the
  prior archive is HDR-fused, so past colour-bar samples can't be recomputed as single.

## Science logging (Tier 0) — `science.py` → `science.csv`

Every 5-min tick, `capture.sh` runs `science.py`, which appends one row to
`science.csv` (LOCAL only, never uploaded — ~tens of KB/day). Columns:

`ts_utc, ts_local, min_local, sun_elev, bright, sky_hex, sky_hue, sky_hue_name,
cloud_frac, gcc, lit_windows, tempF, humidity, windMph, cloud_cover, weather_text`

- **sky_hex / sky_hue** — average colour / sat-weighted hue of the top 10% (open sky).
- **cloud_frac** — % of the top 40% that is whitish (low-sat, bright). Uncalibrated
  proxy (pale near-horizon sky reads high); logged alongside Open-Meteo `cloud_cover`
  so the two can be regressed/calibrated later.
- **gcc** — green chromatic coordinate g/(r+g+b) over a lawn ROI (rows 66–82%, cols
  30–95%) → vegetation green-up/senescence across seasons.
- **lit_windows** — count of window-sized bright blobs (area 4–3000 px) on the façade
  band (rows 52–82%). **Night-only**: blanked when sun_elev > 2° (bright façades are
  meaningless). A relative "lit-window index", not an exact count.
- ROIs are fractions of the frame in `science.py` — **re-tune if the camera is re-aimed.**

This tiny log is the substrate for the science ideas (cloud climatology, twilight
colour vs air quality, phenology, occupancy from lit windows, camera-vs-forecast
calibration). Tier 1 (an hourly *standardized* fixed-exposure frame for radiometry)
is not built yet — add if/when a project needs comparable pixel values.

### Colour-of-the-day bar
`science.py` maintains `skycolor.js` = `window.SKYCOLOR={date, samples:[[min,hex]],
isamples:[[min,hex]]}` — today's 5-min average colours, reset at local midnight:
`samples` = **sky** (top-middle-sixth, `goldpeak.sky_region`), `isamples` = **whole
image** (full-frame). Uploaded with the weather batch (~15 min). **Both** dashboards
(public + local kiosk) draw **two** bars under "Sun & golden hour" — average sky colour,
then average image colour — midnight (left) → midnight (right), each sample a colour
column with a current-colour blob. Replaced the old sun day-arc.

**Colour averaging is done in OKLab** (`goldpeak.avg_hex` / `_mean_oklab`): sRGB →
linear → OKLab, average L/a/b, back to sRGB. This is the perceptual mean colour (avoids
the dark/muddy bias of averaging gamma-encoded sRGB bytes). The reported hues
(`sky_hue` / `img_hue`) are the **HSV hue of that OKLab-mean colour** (kept in the HSV
convention so `hue_name` stays intuitive; naturally chroma-weighted since grey pixels
don't shift the mean a/b). Exceptions kept on raw sRGB DN on purpose: **GCC** (phenocam
convention) and the blend peak-selection score's warm/sat/bright (tuned heuristics).

## Plan

Started ~2026-07-13. Run ~1 week, then **reassess the golden-hour criterion** using the
richer 1-min data (the current golden-blend is a first cut; `golden_candidates/` on the
local Desktop/MBP has the comparison material). Movie source is the 5-min archive
(+ the saved daily `peak_<which>.jpg`).
