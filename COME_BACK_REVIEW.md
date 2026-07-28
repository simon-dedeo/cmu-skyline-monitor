# COME_BACK_REVIEW.md — re-entry guide after the 2026-07-21 session

*Written 2026-07-21. **Supersedes the 2026-07-20 version** (that one is preserved as the
history of how the spot program started; this one reflects the live system now). Read this +
`SPOT.md` on return. The "When you're back" checklist is the actionable part.*

---

## Headline: the spot correction is LIVE and public

Per your call (overriding SPOT.md's shadow-mode gate), **spot correction is turned ON** on pandr
(`~/monitor/.spot_apply_on`). Every 5-min tick and every golden-hour frame now serves a
**spot-cleared HDR** to the kiosk, the site dashboard, and photos.proofsandreasons.io. One spot
only (the known window blemish). A daily refiner on **akdeniz** keeps the spot model current, and
an aim-drift check rides along with it.

Correction now has **two layers**: per-frame self-calibration (removes prominent spots on
bright/clear frames) **plus a model-based depth floor** (the refiner's characterized depth + shape
at the drift-tracked location) that still removes the spot when a single frame's dip is at the
noise floor — so **dim/overcast days are now handled too** (verified live). Both are capped at the
local sky level, so neither can create an inverse bright spot.

**Remaining imperfection:** a faint **rim** can appear — that's recent **camera drift** moving the
blemish slightly off the corrected footprint. It should settle as the mount stabilizes, and the
**7-day fit window** (added today) keeps the refiner tracking the current location, so the
correction re-centers automatically as the model updates.

---

## What changed today (one paragraph)

Designed and shipped a real spot corrector: per-bracket, per-channel, footprint-confined
flattening applied to the RAW brackets *before* Mertens fusion (because `enhance()`/CLAHE
amplifies a fused dip ~3×, so correcting post-fusion under-corrects). It self-calibrates depth
per frame (handling the tone-curve exposure dependence) and has a brightening-cap guard so it can
never create an inverse bright spot. Verified across a full day of hourly frames + a clear→cloudy
day sweep. Built a whole-sky Bayesian analysis on akdeniz (32-core box, now part of the fleet):
with a proper smooth-background model there is **one confident compact blemish** plus a few faint
candidates near the frame edges (unconfirmed — likely building-edge structure). Stood up a **daily
refiner** cron on akdeniz. Cleared the old-camera golden hours (≤ Jul 16) from the public gallery.
Set the golden morning/evening **target elevations from two hand-picked frames**. And chased a
"very dark HDR" scare to ground: it was **cloud cover**, not the correction. Then closed the loop:
added the **model-based depth floor** so dim/overcast spots are removed too (verified live);
switched the refiner to a **7-day fit window** (drift-robust) with an initial `spot_model.json`
seeded from the whole-sky fit; and fixed the public **recent-hours page** on santafe (its dashboard
link was a broken relative path, and it still listed the ≤Jul16 old-camera goldens).

---

## The machine map

| Where | What |
|---|---|
| **pandr** (MacBook Air, CMU) | The live monitor. launchd jobs: 5-min tick, goldscan, web (:8787), kiosk, reverse tunnel, nightly spot fit (03:40). Spot correction now ON. |
| **akdeniz** (`akdeniz.lan.cmu.edu`, 128.2.64.5, simon@) | **NEW. 32-core / 131 GB Linux compute box.** Heavy Bayesian/Stan work + the **daily spot refiner** (cron 10:00 UTC). Bidirectional key access with pandr. cmdstan + cv2 + numpy installed. |
| **ganesha** | Serves photos.proofsandreasons.io (live sky.jpg + golden). Nightly spot-lab backup at `/mnt/anopotamia`. Tunnel endpoint :2202. |
| **santafe** | Small-data + golden backup. Non-critical. |
| **proofsandreasons.io** | GitHub Pages (dashboard.html = live HDR hero + colour bars; hours.html = golden gallery). |

**Remote access:** `ssh -J simon@ganesha.lan.cmu.edu -p 2202 proofsandreasons@localhost`
**Compute offload:** run heavy jobs on akdeniz, never on the live Air. pandr→akdeniz and
akdeniz→pandr are both key-authorized.

---

## Daily automation (runs unattended)

- **Spot refiner** — akdeniz cron 10:00 UTC (`~/refiner/daily_refiner.sh`). Pulls pandr's
  `flatfield_acc` (rsync `--delete`, bounded), rebuilds the whole-sky deficit map from the
  **last 7 days only** (changed today — drift-robust), refines the one spot, sanity-gates, and
  pushes `spot_model.json` → `pandr:~/monitor/`. pandr's `flatfield.py::_spots()` reads it,
  falling back to the known-good seed if it's missing or fails the gate (so a bad fit can never
  degrade the live correction).
- **Aim-drift check** — folded into the refiner: reports current drift, warns if > 40 px,
  carried in `spot_model.json.drift`. (drift.py still logs every tick to `drift.csv`.)
- **Golden hour** — goldscan archives every ~2.5-min scan as `archive/<date>/HHMM_gold.jpg`
  (kept indefinitely — a rich candidate pool). goldpeak now selects the frame nearest a
  **per-window target elevation**: **morning +3.46°**, **evening −0.63°** (set from your picks
  1032U and 0044U on 07-21). Peak uploads to ganesha + santafe at window close.

---

## When you're back — checklist

**1. Correction health.** Look at the live dashboard — is the spot gone on a clear frame?
```
ssh proofsandreasons@pandr... 'ls -la ~/monitor/.spot_apply_on'        # flag present = ON
tail -40 ~/monitor/monitor.log | grep -E "spots corrected|FAILED"
```
On a bright/clear frame the spot should be essentially gone. On dim/overcast it may faintly
persist (noise-floor limitation — see below). Watch for the drift **rim** shrinking as the
mount settles.

**2. The refiner.** One block per day in `akdeniz:~/refiner/refiner.log`:
```
ssh simon@akdeniz.lan.cmu.edu 'tail -20 ~/refiner/refiner.log; echo ---; cat ~/refiner/spot_model.json'
```
Look for `REFINE pass` (updated the model) vs `reject` (kept previous — sane fallback), and the
`AIM-DRIFT` line. Check `pandr:~/monitor/spot_model.json` matches.

**3. Aim drift.** `column -t -s, ~/monitor/drift.csv | tail -50`. Today's baseline drift was a
few px, but the rim you saw means it moved a little. If total drift > ~40 px, re-aim + re-pin
the baseline + re-check ROIs and the spot seed.

**4. Golden.** Check the public gallery shows only new-camera goldens, and the morning/evening
peaks now land at the warmer light (your 06:32-ish morning, not 06:06).

---

## Longer-term tasks (parked, by your direction)

1. **Spot correction — the corrector is now transmittance-divide (2026-07-22).** The primary path
   DIVIDES by the refiner's empirical per-pixel transmittance map (`spot_tmap.npy`) instead of
   reconstructing a smooth ring background. This closed three things at once: (a) **dim/overcast**
   (depth floor, then the map), (b) **clouds behind the spot** — division preserves cloud texture
   instead of erasing it or skipping (was the ring-gate failure), and (c) **drift rim** — the map
   is rebuilt daily from recent frames so it tracks the current location. Validated across
   dawn/gray/partial-cloud/heavy-cloud/haze and live. **Still open:** exposure-resolved maps (the
   multi-exposure data — 3 exposures/tick + daily ladders — is being SAVED but not yet used to make
   the map exposure-dependent); and if the drift rim ever persists, re-pin the aim baseline.
2. **Brighter HDR on cloudy days (experiment).** You like the sun-elevation exposure reference,
   but on overcast days the fixed-per-elevation exposure reads dark by design. Idea: use the
   **weather cloud info** (already in science.csv / the weather feed) to lengthen the DISPLAY
   bracket exposure on cloudy days — while keeping the science reference fixed for radiometric
   comparability. Experiment when there's time.
3. **Whole-sky multi-spot model.** The akdeniz analysis suggests 1 solid spot + a few faint
   edge candidates. Confirm/reject the candidates with a transmission-vs-occlusion test (does
   each scale with sky brightness like a blemish, or stay fixed like a building edge) before ever
   adding them to the corrector.
4. **Local golden gallery page.** Thumbnails are at `~/Desktop/MBP/golden_gallery/g/` (how you
   picked 0044/1032); the `index.html` + larger views were not finished.

---

## Key facts / gotchas from today

- **Dark HDR = weather, not a bug.** The reference exposure is fixed per sun-elevation *by
  design* (a cloudy noon reads darker — real signal); CLAHE amplifies it. Proof it wasn't the
  correction: fusing without correction was equally dark, the raw reference was dark, and the
  dimming began before correction was enabled and tracked clouds rolling in. `cloud%=0` was the
  cloud detector misfiring on a dim uniform frame (minor separate issue).
- **Correction is pipeline-safe:** `flatfield_acc` and `spotlab` read the RAW `sky_ref.png` +
  raw brackets (capture.sh moves them before any correction), so the refiner/measurement are
  never blinded by the correction. **When touching this, keep it that way.**
- **The corrector** (`flatfield.py`): per-bracket, per-channel, footprint-confined flatten in
  linear light, brightening-capped (`add ≤ B−lin`, no inverse spot). Gates: ring-uniformity
  (skip cloud edges) + `DIP_MIN=0.004` (skip noise-floor dips). Backups:
  `flatfield.py.bak-{20260721,v5,guard,dipmin}`.
- **zsh gotcha:** on pandr (zsh) an unquoted `$FS` of newline-separated paths is NOT
  word-split — pass bracket globs directly (`..._br*.png`) to hdrfuse, don't build a var.
- **Numbers:** clear-sky display spot dip ~6.7% → ~0.8% corrected. Whole-sky fit: known spot at
  ~(1184,174), ~5.7% amplitude, r≈33–40 px. Golden targets: morning +3.46°, evening −0.63°.
- Full technical detail is in the memory file `window-spot-program.md` and `akdeniz-host.md`.
