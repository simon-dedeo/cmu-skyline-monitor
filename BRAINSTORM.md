# CMU Monitor — Vision R&D brainstorm (park until we have data)

*Ideas explored 2026-07-17 with Simon for what the M1 machine + a future fast camera
could do scientifically. **Deliberately parked** — the plan is to accumulate lots of
data with the new camera first, then revisit what's actually possible. Nothing here is
on the setup critical path; `TODO_NEW.md` stays clean for the actual bring-up.*

*Prototype scripts: `~/monitor/experiments/` (`cloudflow.py` = cloud-motion→wind,
`deform.py` = tracking + divergence/vorticity, `montage.py` = contact sheets). Full
lab notes in memory `cloud-wind-experiments.md`.*

---

## The load-bearing finding: cadence, not compute, is the limit

On the best archived cloud day (7/13 afternoon cumulus,
`OLD-MACHINE-DATA-2026-07-17/ARCHIVE-2026-07-15/archive/2026-07-13/`, ~1540–1900Z),
clouds move **50–150 px per 5-min step AND reshape substantially** between frames.
Consequences measured: dense Farnebäck flow fails on that displacement (~0);
phase-correlation barely locks (6/47 pairs); **Lucas-Kanade pyramidal feature tracking
WORKS** (maxLevel=5, winSize=31, forward-backward reject → 200–290 clean cloud tracks
per pair). But only 3/80 consecutive pairs were directionally coherent, and
divergence/vorticity maps of the 5-min field were pure noise. → **The new fast camera
(~10–30 s, or video-rate) is the single biggest unlock**: small per-step displacement +
little deformation between frames = clean tracking AND a differentiable flow field. The
M1 does a flow computation in milliseconds, so real-time per-frame processing is trivial
— compute is not the limit, cadence is.

## a) Cloud motion → wind
LK-track clouds (cloud mask: HSV value>150 & sat<70; top-42% sky ROI; temporal-median
background subtraction to kill static structures), median the vectors → dominant
advection. **Point a camera at the ZENITH, not the skyline** — simplest geometry
(motion ∝ wind/height, no foreshortening), a true overhead wind map. `cloudflow.py`
currently reports an IMAGE-relative bearing (honest), not true compass — see (f).

## b) Cloud deformation → turbulence (real; needs fine cadence)
Differentiate the tracked velocity field u(x,y): divergence ∇·u (growth/updraft-spread
vs convergence), vorticity/curl (eddies, shear), strain/deformation tensor (stretch +
shear). Turbulence proxies: velocity structure function ⟨|Δu|²⟩ vs r (look for
Kolmogorov r^(2/3) inertial range), enstrophy, TKE from velocity variance, eddy-size
spectra. Caveats: it's 2-D PROJECTED (line-of-sight) deformation, not full 3-D; and use
the TRACKED velocity field, not raw brightness change (which conflates air strain with
condensation/evaporation microphysics).

## c) Cloud-type classification (cumulus vs stratus/cirrus/…): feasible
Two routes, best combined: (i) classical texture/color/coverage features — edge density,
GLCM/LBP texture, brightness histogram, grey-vs-white, spatial-frequency spectrum,
sky-cover fraction (science.py already has a crude `cloud_frac`); (ii) a small CNN
trained on a public genus dataset — **CCSN** (Cirrus/Cumulus/Stratus/Nimbus, 11 classes,
~2.5k imgs) or **SWIMCAT** (5 whole-sky classes) — converted to **CoreML** → runs on the
M1 Neural Engine in ms. **Motion is itself a discriminator** (ties to a+b): cumulus =
strong deformation + convective divergence + moderate drift; cirrus = fast drift (jet
stream), little deformation, high/thin/streaky; stratus = near-stationary, low texture.
So a texture+motion feature vector → classifier is a natural fit. **Needs a wide/zenith
view** to be meaningful (genera are altitude+shape defined; the narrow skyline patch is
poor). 8 GB RAM → small/quantized models only.

## d) Altitude (for ABSOLUTE speed; single cam gives only angular rate + direction)
- **Stereo/parallax — gold standard.** Two cameras on a known baseline (the two Macs on
  different rooftops!) both looking up, match a cloud feature, triangulate base height;
  height + angular motion → true wind vector (cf. UCSD whole-sky imager nets). Reuses the
  retired old Mac.
- **LCL from thermodynamics — FREE & NOW.** Cumulus base ≈ 125 m × (T − dewpoint °C); we
  already log temp/dewpoint/humidity. Rough base → angular→m/s immediately.
- **METAR** (KPIT/KAGC) reports layer heights hourly (free API) — direct input/check.
- **Shadow speed (see e + f):** a cloud's shadow sweeps the ground at the cloud's true
  horizontal speed (parallel sun rays); measured over CALIBRATED ground landmarks it gives
  wind in m/s directly, and height = shadow_speed / cloud_angular_speed.

## e) Shadow geometry — OPPORTUNISTIC ONLY, not a backbone
Pittsburgh rarely gives hard-edged shadows (mostly hazy/overcast/soft cumulus), so this
can't be a routine altitude source. Treat it as an auto-triggered cross-check on the rare
days that qualify (high cloud-edge contrast + high sun — both cheaply detectable, so the
system can *decide by itself* when to attempt it and otherwise skip). The physics, when it
applies: sun at elevation α (from `solar.py`), cloud at height h casts a shadow displaced
d = h/tan(α) in the anti-solar azimuth; more usefully, shadows sweep the ground at the
cloud's true horizontal speed → measured over calibrated landmarks (f) gives wind in m/s
and height = shadow_speed/cloud_angular_speed. Don't invest here until a–d work.

## f) Landmark / camera-pose calibration (use public CMU data)
NB the SUN is NOT in view (camera looks at the skyline; sun is above the FOV), so
calibrate from the **static landmarks that ARE always in frame** — the Judith Resnik
memorial, the roof "triangle" gable, CMU building corners/rooftops. Get their 3-D world
coords from public data (OSM footprints + heights, campus GIS, USGS/PA DEM for elevation),
mark their pixel positions once (a small tool can reuse the aim.html plumbing), then
**`cv2.solvePnP`** → full camera pose (orientation/tilt/roll + focal length/FOV). For wind
DIRECTION alone you barely need this — just the true compass bearing to 2–3 landmarks (off
a map) vs their pixel-x. Unlocks: pixel→true azimuth/elevation, image bearing→true compass,
cloud/shadow georeferencing, and the ground-plane homography for (h). **Genuinely ONE-TIME**
(landmarks never move) — done after the mount is fixed, re-done only if `drift.py` flags a
mount shift. It is the connective tissue for a–e and h, and it is not recurring tuning.

## g) AUTONOMY (Simon's requirement — no ongoing hand-tuning)
Design rule: one-time calibration is fine; recurring per-day tuning is not. Three habits:
- **Self-gating, not tuning.** Every estimate ships with a confidence metric (tracking
  consistency R, #tracks, cloud fraction, forward-backward error). In bad conditions
  (overcast, no texture, night, incoherent motion) the system **abstains** ("no confident
  wind this tick") instead of emitting a tuned-to-look-right number. Same fail-soft
  discipline the monitor already uses. Fixed thresholds set once generalize; never chased.
- **One-time calibration, not recurring tuning.** The sun isn't in view, so it can't
  auto-calibrate pose — but the reference is the static landmarks always in frame, so pose
  is fixed ONCE (f) and untouched unless the mount physically moves, which `drift.py`
  auto-detects and flags. Exposure/WB handled by the existing reference-frame machinery.
  (Optional sun-free nicety: the sky's Rayleigh brightness/color gradient gives a rough
  sun-azimuth sanity check with no sun disk — but landmarks make it unnecessary.)
- **Train-once, infer-forever.** The cloud-type CNN and any detector are trained once
  offline; inference is parameter-free. LCL height is a pure formula.
Autonomous stack: LK wind (self-gates on R) + LCL height (formula) + CNN cloud-type
(trained once) + optional stereo (auto feature-matching). Shadows and manual
landmark-clicking are NOT on the autonomous critical path.

## h) Pedestrian dynamics (group travel, bottlenecks)
Technically very doable and reuses the calibration + autonomy pattern; the science is rich
(group/co-mover detection, fundamental diagram speed-vs-density, bottleneck flow rates,
lane formation, route choice) and squarely Simon's collective-behavior wheelhouse. TWO
real constraints found by inspecting a midday full-res crop of the courtyard/lawn:
- **Framing/scale:** the current view is skyline-aimed — pedestrian areas (terraced
  walkways, steps, a plaza; a utility cart was visible) are distant, oblique, and small
  (~15–30 px/person at 1920 px; ~30–60 at 4K), with lots of roof/building filling the
  frame. Fine for detection, marginal for group/pose detail. Want a DEDICATED camera aimed
  at one plaza / path junction / doorway bottleneck — closer, more top-down, high-res.
- **Frame rate is the hard requirement:** walking people need ~video rate (several fps),
  NOT the 5-min / 2.5-min cloud cadence — between slow frames a person is gone or
  unmatchable. The new USB-3 cam enables it; process frames in REAL TIME and store only
  trajectories (small + privacy-friendly), discarding raw video.
Pipeline: YOLO person detection (→CoreML on the M1 Neural Engine; tile/crop for small
people) → multi-object tracking (ByteTrack/OC-SORT) → ground-plane homography from the
SAME landmark calibration (f) → metric trajectories (m, m/s) → group & flow analysis.
Autonomy: detector trained once, self-gate to log only when people present, one-time
homography.
**PRIVACY / IRB — address BEFORE deploying:** tracking people, even in a public campus
space, is human-subjects observation. Consult CMU IRB. Privacy-by-design: process
on-device, store ONLY anonymized trajectories/aggregates (never faces or raw imagery), no
re-identification — and at this range/resolution faces aren't resolvable anyway. Flagged,
not decided — Simon's call + IRB.
