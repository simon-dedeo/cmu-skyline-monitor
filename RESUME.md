# CMU Skyline Monitor — pick-up notes (paused 2026-07-13)

Read this first when resuming. Companions: `monitor/GOLDEN.md` (full current-system
detail), `REPORT.md` (original build), and Claude's project memory (auto-loaded).

## One-line status
Everything is deployed and running unattended for ~a week. The plan: **let it collect,
then review the golden-hour picks + the single-vs-adaptive frames + `science.csv`, and
decide what to change.** Resume date target: ~2026-07-20.

## How to pick up (access + health check)
- SSH to the Mac: `ssh laboratoryforsocialminds@laboratorys-mbp.wifi.local.cmu.edu`
  (key-based, no sudo). Code lives in `~/monitor/`; local mirror is `/Users/simon/Desktop/MBP/monitor/`.
- Public pages repo: `/Users/simon/Desktop/PROOFS/proofsandreasons.github.io` (GitHub
  Pages). Dashboard: https://proofsandreasons.io/dashboard.html · Homepage: https://proofsandreasons.io
- santafe upload target: `simon@santafe.santafe.edu:~/html/` (key `~/.ssh/id_rsa` on the Mac).
- **Is it alive?** `launchctl list | grep lsm` (want monitor + goldscan + monitor-web);
  `tail -30 ~/monitor/monitor.log`; the dashboard footer shows a green "system up" dot
  if the featured image is < 20 h old.
- Handy: `launchctl kickstart -k gui/$(id -u)/com.lsm.monitor` (force a tick);
  `rm ~/monitor/.last_upload` (force a weather upload); cheat-sheet in `GOLDEN.md`.

## What's running (the week experiment)
- **com.lsm.monitor** (5 min): capture sky (adaptive HDR) + save the standardized single
  frame (`sky_std`), archive both every 5 min, night-boost variant when dark, run
  `science.py` (→ `science.csv` + `skycolor.js`), upload weather + colour (batched,
  compressed) ~every 15 min. Courtyard cam is UNPLUGGED (fails soft).
- **com.lsm.goldscan** (1 min): during golden windows, scan every minute, keep the running
  peak locally; at window close upload the single best "peak golden" frame + a dated copy
  + `golden.js` + `goldindex.js` (one connection). Featured image + "recent hours" page.
- Disabled (moved to `~/Library/LaunchAgents/disabled-by-claude/`): experiment, timelapse.
- Uploads audited at ~98 connections/day, ~1.5–2 MB/day. Colours averaged in **OKLab**.

## Data being collected for review
- `~/monitor/archive/YYYY-MM-DD/HHMM_{sky,sky_std,sky_night}.jpg` — 5-min frames (HDR/adaptive,
  the standardized single, and night-boost) + `peak_<which>.jpg` per window.
- `~/monitor/science.csv` — per-tick metrics (sun elev, sky/img colour+hue, cloud%, GCC,
  lit-window count, weather). Local only.
- santafe accumulates `peak_<date>_<which>.jpg` (the golden pairs on hours.html).

## Review at end of week (checklist)
1. **Golden-hour picks**: do the auto-picked peaks (hours.html / dated peaks) look right?
   Compare against the archived 1-min frames of each window. Is `blend` choosing well?
2. **Single vs adaptive HDR**: diff `archive/…_sky.jpg` (adaptive) vs `…_sky_std.jpg`
   (single) — did adaptive-HDR ever help, or is single-frame always better? (See TODO #3.)
3. **science.csv trends**: cloud% vs Open-Meteo cloud_cover (calibrate), GCC (green-up),
   lit-window count overnight (occupancy), twilight sky-colour path.
4. **Rebuild the movie** from the 5-min archive (see the earlier 15-min movie recipe).
5. **Re-tune ROIs** in `science.py` if the camera was bumped/re-aimed (lawn + façade boxes
   are fixed fractions).

## TODO / future ideas

### 1. Google Scholar "cited by" widget (grant 63750)
Goal: auto-surface recent papers that cite **Templeton Grant 63750 ("Explaining Universal
Truths")** and add them to the homepage "Recent Outputs" (or a new "Related / citing work"
panel).
- **Google Scholar has no API and blocks scrapers** (`scholarly` is fragile/CAPTCHA-prone;
  SerpAPI works but is paid). Prefer official free APIs:
  - **OpenAlex** (free, no key): filter works by funder = John Templeton Foundation +
    award/grant id, or full-text search the phrase/number. Best coverage + funder+award metadata.
  - **Semantic Scholar** (free API): search + citation graph.
  - **Crossref** (free): funder registry, filter by award number.
- Matching caveat: bare "63750" is ambiguous → match funder=Templeton AND award 63750, or
  the acknowledgement phrase "Explaining Universal Truths"; keep a manual-approve queue
  (flag candidates, don't auto-publish false positives).
- Where to run: cleanest is a **GitHub Action (daily cron)** on the pages repo that hits
  OpenAlex, commits `citing.json`, and the homepage renders it (decoupled from the Mac).
  Alternatively a small launchd job on the Mac → `citing.js` → santafe (like golden.js).
- Poll daily/weekly (citations appear slowly).

### 2. Better "optimal golden hour" detectors (brainstorm — try on the week's data)
Current = golden-blend `sat*(warm+15)*exp(-((bright-110)/55)^2)`, peak over the 1-min window.
Ideas to compare against the archived frames:
- **Warm-cloud illumination**: fraction of sky pixels in amber/pink hue with high value →
  rewards clouds catching warm light (not just clear warm sky).
- **Chromaticity / colour-temperature path**: peak = max warm shift (lowest correlated
  colour temperature) of the sky — a principled "how golden is the light".
- **Raking-light / directional contrast**: long shadows + side-lit façades; measure façade
  brightness gradient / local contrast — captures the dramatic 6:31-style look.
- **Sun-elevation prior**: weight by proximity to ideal sun elevation (~0–6°) and pick the
  best-looking frame within that window (sun angle is the physical definition).
- **Aesthetic score**: colourfulness (Hasler–Süsstrunk) + contrast + cloud texture/entropy;
  or a small NIMA-style aesthetic model.
- **Learn-to-rank**: rate a handful of the week's frames, fit the blend weights to taste
  (the `golden_candidates/` review flow is the training data).
- Likely answer: combine raking-light + warm-cloud + a sun-elevation prior; tune to taste.

### 3. HDR — checks, interesting approaches, downsides of the current method
Current sky HDR = **adaptive Mertens exposure-fusion** (fuse `[1/3,1,3,9,27]×E*` only when
the single frame's building band < 90; else keep the single frame). `fuse()` drops
off-resolution frames (fixed the dawn banding bug). Science/colour use the single frame.
- **Downsides of what we have**:
  - Mertens fusion **desaturates** the sky (measured 13% vs 21%) and can look flat/greyish;
    tone-mapping isn't radiometric (why science uses the single frame).
  - **Cloud-motion ghosting**: clouds move between the 5 bracket grabs → soft/doubled clouds
    (we fixed the resolution-stretch ghost, but motion ghosting can remain).
  - Extra camera time (5 grabs) → more torn-frame / contention risk.
  - The building-band<90 adaptive threshold is a heuristic; may mis-trigger.
- **Interesting alternatives / checks**:
  - **Luminance-only HDR**: apply HDR to luminance, keep the single frame's chrominance →
    dynamic range without desaturation (best of both).
  - **True radiance HDR (Debevec) + a good tonemapper** (Reinhard/Mantiuk) — physically
    meaningful, could feed science; but tonemap choice matters.
  - **De-ghosting**: `cv2.createAlignMTB` + de-ghost exposure fusion, or faster brackets.
  - **Fused-frame quality gate**: detect banding/ghosting (high-freq horizontal energy, or
    compare vs the single frame) and fall back generally (we only guard resolution now).
  - **Occasionally archive the raw bracket** (a few sets/day, disk permitting) to try
    tonemappers offline without re-capturing.
  - **Is HDR even worth it?** The week's adaptive log shows how often it fused vs kept single
    — maybe single-frame is enough ~always and HDR is only for rare overcast dusk.

## Smaller open items
- `systemstats` showing high CPU was a false alarm (flat CPU-time); no action. A reboot
  would clear it and also refresh the 109-day uptime if desired.
- Security patches (11.7.11 + Safari) and `sudo pmset -a sleep 0` still need Simon's sudo/GUI.
- Kiosk (`bash ~/monitor/kiosk.sh`) installed but not auto-launched.
- Courtyard cam unplugged — replug + re-enable in `update.py`/`aim.py` if wanted (its ROI
  handling is already optional/soft-fail).
- Access-log idea (who views from non-Pittsburgh) was dropped; PHP does work on santafe if
  revisited.

## 2026-07-14 incident (mid-pause)
- **GS sky cam wedged**: corrupt banded frame at 6:12 AM scored blend 1067 → auto-uploaded as morning peak (fixed: public golden repointed to clean 6:10 AM frame, blend 546; corrupt frame kept at `archive/2026-07-14/peak_morning_corrupt_0612.jpg`). Camera stopped delivering frames at 6:38 AM; captures hang; device enumerates as "…GS **#2**" phantom; libusb reset fails (kernel claim). **Needs hands: `sudo killall VDCAssistant` on the Mac, or unplug/replug the camera USB.** Kiosk/live sky.jpg frozen since 6:36 AM until then.
- **solar.py fixed**: sun/golden times now keyed to the LOCAL (America/New_York) calendar date, not UTC — the UTC keying flipped data.json to the next day's windows at 00:00Z (8 PM EDT), so goldtick finalized every evening window ~8:05 PM and never scanned through sunset. Deployed + verified on the Mac.
- **TODO (from review)**: goldpeak has no corruption gate — a garbage frame can win the window (saturation/warmth explode). Consider a sanity check (e.g. row-to-row banding detector, or reject blend >2× the window's running median). Also: goldscan's 1-min frames are deleted at window close — only the 5-min archive + peak survive for review; archive them (or their scores) if full-granularity review matters.

## 2026-07-14 afternoon — golden cadence rework + camera self-healing (deployed)
- **Golden cadence is now 2.5 min, single-owner**: com.lsm.goldscan fires every 150 s; during a window goldscan owns the camera — scores the frame, refreshes kiosk sky.jpg (atomic), archives EVERY scan as `HHMM_gold.jpg`, and saves its capture meta to `.last_gold_meta.json`. The 5-min tick sets `GOLD_SKY_REUSE=1` and update.py reuses that frame+meta instead of capturing (falls back to capturing if the frame is >300 s stale). Net: 2 multi-exposure captures per 5 min in windows (was ~6) — the dawn duty cycle is what wedged the cam.
- **Camera-wedge watchdog** in capture.sh: sky.jpg >240 s stale for 3 consecutive ticks (~15 min) → `sudo -n killall VDCAssistant` → `usbreset.py` (libusb device reset; only works after the daemon kill releases the claim) → killall again. Plus a **pre-window daemon reset** in goldtick.sh (first tick of each window, under camlock — can't interrupt a capture).
- **sudoers grant is argument-exact**: `laboratoryforsocialminds ALL=(ALL) NOPASSWD: /usr/bin/killall VDCAssistant` — only that literal command is passwordless; `killall -0 ...` etc. still prompt.
- **Camera still DOWN pending physical replug** (firmware lockup survived bus reset; only VBUS removal clears it). After replug everything self-resumes; watchdog fires harmlessly every ~15 min until then.
- **Simon's peak-golden ground truth so far** (for tuning the detector later): evening ≈ **8:20 PM EDT, maybe a bit later** (scorer's retro pick 8:51 was too late, 7:36 too early); morning ≈ **6:10–6:15 AM EDT**. Plan: more metering from the archived gold frames, then a detector from **sun position + image hue**.

## 2026-07-14 late — reference frames + campus colour (deployed)
- Colour bars now read `sky_ref.jpg`: fixed sun-elevation-keyed exposure (expose.py `elev_table`), captured each 5-min tick, archived to `archive_ref/DAY/HHMM.jpg` (keep all — back-analysis). Second bar = **campus colour** (rows 55–100%, below the skyline; skycolor.js key "isamples" unchanged). CSV rotated: science_v1.csv (old), science.csv (new schema + campus_hex/ref_exp/ref_gain).
- TODO: tune elev_table dawn/dusk rows against science.csv once a few golden hours accumulate; consider campus hue in the golden caption (currently still whole-image img_hue); watch for WB drift (no explicit WB control — assumed module-fixed).
