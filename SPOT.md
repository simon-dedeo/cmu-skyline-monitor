# SPOT.md — Window-Blemish Characterization & Correction Plan

*Status 2026-07-20: correction in served images is **DISABLED** (flag file `~/monitor/.spot_apply_on`
absent) while this plan gathers data. Phase-0 instrumentation implemented today: per-tick spot
photometry (`spotlab.py` → `spot_lab/spot_photometry.csv`), 448×448 crops of spot + 4 control
patches × 4 frames/tick, bracket-frame archive ring, exposure/gain ladders, aim-drift CSV.
Known spot: (1163, 187) @4K, ~2% encoded-space dip midday. This plan was produced by a
3-lens design panel (measurement design / physical optics / Bayesian modeling) + adversarial
critique + synthesis, 2026-07-20.*

*Day-1 calibrations MEASURED 2026-07-20: **A_amp = 10.6** (injected 0.20% linear dip →
2.13% in the Mertens+CLAHE display frame) ⇒ **linear-space residual target = 0.028%**.
First bracket photometry: spot dip_lin ≈ 3.2–3.9% roughly exposure-invariant across rungs
(encoded dip 1.5–2.0%), consistent with §0's t + s/L_bg invariance. Control patches read
±0.3–0.6% single-frame scatter (the empirical σ_meas baseline). θ_⊙ ephemeris scan still
pending (needs camera-pose calibration).*

*ARCHITECTURE (Simon 2026-07-20): **all computation runs locally on the MacBook Air** —
no ganesha dependence for fitting (supervision may be absent for ≥1 week). Inference in
**R + Stan** (R 4.6.1, cmdstan 2.39 via cmdstanr, installed on the Air). Nightly launchd
job `com.lsm.spotnightly` (03:40 local): `spotfit.R` (Stan M2 per channel + M1
falsification; robust-rlm fallback if Stan breaks) + `spotmap.py` (per-pixel spot map —
spots are NOT assumed circular or even: window blemishes are small irregular dots, so the
primary spatial representation is the empirical per-pixel transmittance map from the crop
stack; any radial/parametric form is only a smoothing prior on top of it) + rsync backup
of spot_lab/ + flatfield_acc/ to **ganesha:/mnt/anopotamia/spot_lab_pandr/** (1.7 TB free).
Remote supervision via the persistent reverse tunnel:
`ssh -J simon@ganesha.lan.cmu.edu -p 2202 proofsandreasons@localhost`.*

*FIRST-NIGHT STAN FIT (2026-07-20, 58 ticks, clear sky, rhat≈1.003): t_B = 0.962
[0.958–0.966], t_G = 0.964 [0.960–0.969], t_R = 0.967 [0.962–0.971] — the t_B < t_G < t_R
wavelength ordering §0 predicts for a brownish absorber. s still consistent with 0, as
expected with no background leverage yet (needs clouds/twilight).*

## 0. Forward model and identifiability (drives every design choice)

A speck on the window at d_w ≈ 10 cm, lens focused at infinity, is imaged as its transmittance
map convolved with the aperture footprint at the window plane (pupil ≈ f/N ≈ 1.5–2 mm). At
~0.02°/px the observed ~80 px radius ≈ 3 mm at the window — consistent with a ≲1 mm speck ⊗
~2 mm pillbox. Consequences: (i) t(x,y) is intrinsically smooth with pillbox shoulders
(correlation length ~25 px) — a low-order radial parameterization is physics, not convenience;
(ii) the ~2% peak dip is speck/pupil **area** ratio ≈ 0.02, i.e. diameter ratio ~0.14; (iii) the
same material scatters ambient light into the pupil through the same footprint, so expect
**s(x,y) ∝ (1 − t(x,y))** spatially — a strong testable constraint.

Per channel c, scene-referred linear: **L_obs = t_c·L_bg + s_c, s_c = β_c·E_win(sun, sky)**.
Brownish absorbers give t_B < t_G < t_R; Mie scatter off µm dust is forward-peaked (s spikes at
small sun–spot elongation θ_⊙) with a blue-heavy tail.

**Identifiability — stated so the wrong version cannot resurface.** Within one bracket both
t·L_bg and τ·s scale identically with exposure τ: in linear units the spot/background ratio
(t + s/L_bg) is **exposure-invariant**, so **the 3-stop bracket does NOT separate t from s**.
The bracket's jobs are: identify the tone curve g⁻¹ and additive offset b (288 free calibration
triples/day), and extend dynamic range. Separation of t from s comes **only from variation in
L_bg at ~fixed window irradiance E_win**: uniform cloud cores transiting behind the spot (L_bg
swings 2–5× in minutes), and the twilight ramp. Regress linearized spot radiance on predicted
background radiance across ticks: slope → t, intercept → s.

**The E_win confound:** a cloud darkening L_bg often occludes the sun simultaneously, moving s
with L_bg and biasing the split. Mitigation: log per-tick E_win proxies — a direct-sun-occlusion
flag (saturated-pixel count near the ephemeris solar position) and the luminance of a sunlit
facade patch — and restrict t-leverage samples to E_win stable within ±5%. Night (urban skyglow
⇒ L_bg small but nonzero, illuminant unlike daytime) anchors **b, the noise floor, and the model
form only** — never extrapolate night ŝ into daytime correction.

## 1. Goals and success criteria

**Day-1 calibrations that define the targets:**
- **Amplification factor A_amp:** inject synthetic ±0.2% dips into real brackets, run
  Mertens+CLAHE, measure display amplification (observed ~4–10×). The **linear-space residual
  target is 0.3% / A_amp ≈ 0.05%**; any looser linear target fails the display criterion by
  construction.
- **Ephemeris scan** of achievable θ_⊙ over the next 60 days; condition strata are defined from
  the achievable range, and correction outside the trained hull falls back (§6).

**Criteria (seeded annulus method on display frames, shadow mode):**
- **S1:** residual |dip| median < 0.3%, p95 < 0.5%, per stratum {clear, broken, overcast} ×
  {morning, midday, evening} + twilight + smallest-achievable-θ_⊙ bin, on ≥5 held-out days
  **collectively spanning** clear/broken/overcast (per-bin residuals pooled across held-out
  days; extend the run rather than relax bins).
- **S2:** residual scatter at the spot ≤ 2× matched control patches (§2.3).
- **S3:** overshoot never > 0.3%; annulus region altered < 0.1%; fail-soft to identity always
  available.
- **Stability:** |Δt_c| < 3 posterior SD across two consecutive weekly refits; changepoint
  monitor green.
- **Re-enable gate:** 7 consecutive shadow-mode days meeting S1–S3.

## 2. Data collection design

**2.1 Per-tick logging** (`spot_lab/spot_photometry.csv`, M1 Air, <1 s/tick). One row per
(tick × frame_role {b−2, b0, b+2, ref} × patch × channel); 1 spot + 4 controls = 60 rows/tick.
Columns: `ts_utc, frame_role, exp_units, exp_ms, gain, linz_version`; geometry
(`patch_id, cx, cy, radii`); photometry (`disk_med_enc, disk_mad_enc, ann_med_enc, ann_mad_enc,
bg_fit_lin` [robust 2D-quadratic annulus fit evaluated under the disk — not ring median],
`bg_fit_resid_mad, disk_med_lin, dip_enc, dip_lin`, 10 radial-bin medians 0–150 px for the spot
patch); quality gates as **recorded booleans, never reasons to skip a row** (`frac_clip_hi>250,
frac_clip_lo<5, gate_ann_uniform, gate_motion`); context (`sun_elev, sun_az, cloud_frac,
sky_med_lin, ewin_facade_lin, sun_occluded_flag, aim_dx, aim_dy`).

**2.2 Crops.** 448×448 lossless PNG (large enough to contain the 110–160 annulus and the
220–300 far ring) around each of the 5 patches, all 4 frames: 20 crops/tick, ~150–200 KB each
⇒ **~1 GB/day**. Keep 21 days full (~21 GB), then decimate to every 4th tick + all twilight;
hard cap 60 GB FIFO. All fitting is reproducible from crops.

**2.3 Control patches.** 4 pseudo-spots in clean sky, same image row band (matched vignetting),
≥100 px above worst-case-drift roofline, off the crane corridor, identical pipeline:
C1 (1900,187), C2 (2400,187), C3 (2900,187), C4 (3400,187). They supply: empirical σ_meas per
condition bin (the **only** noise input to power calculations and fit weights — not shot-noise
arithmetic), the S2 null, the §5 detection floor, and drift alarms.

**2.4 Full-frame bracket archive.** 14-day ring (`spot_lab/brackets/`), ~154 GB. Detection
stacks (§5) read the **b0 frames of this ring**; additionally extend ref-PNG retention
(`flatfield_acc/`) to 14 days (+3.7 GB/day) as backup — `archive_ref/` JPEGs are unusable
(chroma blocking at blob scales). Nightly `df` assertion: below 60 GB free, abort archiving
(never capture).

**2.5 Dedicated ladders.** 8-point ×2 geometric exposure ladder (top rung below sky clipping),
4/day (solar noon, noon±3 h, civil twilight) + 1 night ladder + gain ladders {0,40,80,120,160}
at dusk and night. ~45 s each. **Lock safety (the monitor is inviolable):** ladders run at the
END of a capture tick while it still holds the camera lock (no collision by construction);
discard ≥3 frames after every exposure/gain change and verify the level actually stepped; any
failure aborts silently and never delays the next tick. Archive ladder crops indefinitely; full
ladder frames 14 days (~0.5 GB/day: ~37 × 13 MB).

**2.6 Duration and power.** With control-patch σ ≈ 0.2% and ~400 gated frames/day spanning a
decade of L_bg, SE(t) ~ 10⁻⁴ in 14 days — precision is not the constraint; **coverage is**.
Minimum 14 days, target 21, gated by quotas: ≥3 clear days (sun-azimuth sweep), ≥3 overcast,
≥2 mixed; **≥300 gated leverage ticks with L_bg spanning ≥4× at E_win proxy stable ±5%**
(uniform-cloud-core definition, §3 — not discrete "transit events", which are weather-hostage);
14 twilight ramps; 14 night blocks.

## 3. Measurement method

- **Geometry:** disk r = 60 px at (1163, 187) (inside the 70–90 px skirt — a larger disk
  dilutes the signal); guard 60–110; annulus 110–160; **far ring 220–300** as a skirt check: if
  the annulus level (after local fit) disagrees with the far ring beyond measurement error, the
  forward-scatter skirt contaminates the annulus — enlarge the guard and refit the profile out
  to where it merges with control statistics. Mask the top 20 image rows out of the annulus
  (lens-shading/ISP edge zone) and use a quadratic (not planar) background fit with an adequacy
  check. Sensitivity: repeat key fits at disk 45 / annulus 130–190; shifts > 0.1% flag leakage.
- **Robust statistics:** medians+MAD; Huber weights; downstream Student-t likelihood.
  **Errors-in-variables:** propagate the background-fit covariance σ(L̂_bg) per tick into the
  regression (attenuation bias in t̂ is worst exactly at low-level leverage ticks). Validate
  with the **synthetic-injection harness** (known (t,s) injected into real annulus stacks) — a
  listed deliverable that also calibrates A_amp (§1) and detection thresholds (§5).
- **Linearization (make-or-break systematic):** start with inverse sRGB + b as a day-1
  hypothesis, not a position to defend. **Exposure-ratio invariance test** on control patches:
  linearized b+2/b0 and b0/b−2 must equal 4.00 ± 0.5% per channel, codes 20–**235** (shoulder
  above excluded), with level coverage accumulated across sun elevations (twilight probes low
  codes; midday b+2 sky is clipped and probes nothing high). Expect to land on a fitted
  **monotone spline g⁻¹** (~16 knots/channel, Debevec–Malik-style from ladders, on ganesha).
  **Run the test at each gain rung** of the gain ladders — ISPs switch tone processing with
  analog gain; per-gain LUT if the spline moves; refuse to linearize or correct at unvalidated
  gain codes. Check the radial profile for a bright overshoot ring (ISP sharpening); if
  present, exclude outer bins from t-fitting and reproduce, don't fight, the halo. Version
  everything as `linz_version`; re-derive all `_lin` columns from crops.
- **Clouds:** the **leverage sample** = ticks passing the annulus-uniformity gate
  (quadratic-fit residual MAD < 3× clear-sky baseline) whose *level* deviates from clear-sky
  expectation — i.e. uniform cloud core covering disk+annulus. Edge-in-annulus frames corrupt
  L̂_bg even with a uniform disk: gated out. Temporal gate: dip deviating > 5×MAD from the
  5-tick rolling median (birds/planes).
- **Night:** 100 ms + high gain; a 2% dip at code ~10 is ~0.2 LSB, but read noise dithers —
  stack 200–500 frames (median-of-means) nightly for sub-LSB s_night + b. Constrains
  b/noise/model form only (§0).
- **Aim drift:** spot photometry is pixel-fixed; veto ticks where the predicted roofline (aim
  CSV + margin) comes within 30 px of any annulus, or drift > 20 px within a bracket.

## 4. Model ladder

All fits in linear units L = (g⁻¹(D) − b)/(τ·G(gain)), per channel, on per-tick sufficient
statistics (M0–M3); spatial profile as a conditional stage.

- **M0:** constant encoded-space gain (current method; benchmark).
- **M1:** L_spot = t_c·L̂_bg. Falsification: regress dip_lin on 1/L_bg across ticks; nonzero
  slope ⇒ additive term.
- **M2:** L_spot = t_c·L̂_bg + s_c — **pooled robust regression across ticks** (never within
  one bracket — degenerate, §0). Per-tick IRLS diagnostics on the Air; nightly refit on ganesha.
- **M3:** s_c(tick) = β_c·[k_d·sky_med_lin + k_b·E_beam(facade/sun-flag) + α·K(θ_⊙)], with both
  K = exp(−θ/θ₀) and Gaussian fitted, selected on held-out days.
- **M4 (hierarchical, R/Stan on the Air):** spatial shape from the EMPIRICAL per-pixel map
  (spotmap.py) — spots are small irregular window dots, not circles: t_c(x,y) = 1 − a_c·Φ(x,y)
  and s_c(x,y) = ŝ_c·Ψ(x,y) where Φ, Ψ are smoothed normalized shapes estimated from the
  pixel map (smoothness scale ≈ the ~25 px pupil footprint is the only physics imposed);
  **test Ψ ∝ Φ** (§0 prediction). Day-level random effects t_{c,d} ~ N(t_c, σ_t²),
  σ_t ~ HalfNormal(0.005) — the dew/rain/cleaning monitor; a fired changepoint splits the
  epoch rather than averaging. Priors: 1−t_c ~ LogNormal(ln 0.02, 0.5²);
  β_c·A_midday ~ HalfNormal(0.01); θ₀ ~ LogNormal(ln 20°, 0.5²); Student-t likelihood,
  per-tick scale from annulus MAD + EIV term.
- **Inference escalation:** MAP + Laplace (minutes); **NUTS only if** Laplace/MCMC SDs disagree
  > 20% on t or θ₀, or changepoint structure is multimodal (< 30 min on the tiny
  sufficient-stat set). A **2D GMRF field is a contingency rung only**, entered iff
  radial-profile PPC residuals show angular structure > 2σ — the realistic failure mode is jobs
  that stop running, not statistical inadequacy.
- **Comparison:** **leave-whole-days-out only** (within-day correlation makes tick-level
  PSIS-LOO anticonservative; use it only as a cheap screen). Accept M(k+1) iff ΔELPD > 4·SE
  **and** held-out *display*-pipeline residual improves. PPCs: residual dip vs L_bg deciles,
  θ_⊙, cloud_frac, hour, bracket rung — flat or reject.

## 5. Other-spot detection

From the 14-day b0 archive: bin by sky_med_lin × sun-elevation (≥30 frames/bin), median-stack
per bin (ganesha; M1 fallback in row-band chunks, ~500 MB RSS); normalize by a σ=100 px
smoothed copy (protects vignetting/gradient); **matched filter with the known spot's radial
profile** (not DoG — suppresses boundary ringing); search only the sky mask eroded 100 px from
the roofline, crane corridor dilated out. **Detection:** same pixel ±5 px in ≥2 radiance bins
AND ≥3 independent day-stacks (disjoint splits — this is what killed the crane/roofline false
positives), amplitude > 5× control-patch stack MAD (calibrated by the injection harness).
**Graduation** (bracket-ratio invariance has zero discriminating power — everything is
exposure-invariant in linear light): (i) dip scales multiplicatively with background radiance
across cloud variation, (ii) stays pixel-fixed while the aim CSV shows the scene moved,
(iii) persistence across splits, (iv) ≥7 days seeded photometry with day-consistent t > 0.2%
dip. Then it joins the registry and the M4 hierarchy (pooling shape hyperparameters, not
amplitudes).

## 6. Correction (posterior → live pipeline)

Applied **per bracket frame, before fusion**, within sky-mask ∩ footprint (t̂ < 0.999):
1. Linearize via validated 256-entry per-channel LUT (`tone_curve.json`, gain-appropriate).
2. ℓ_c = (ℓ − b − τ·ŝ_c(x,y; A, E_beam, θ_⊙)) / t̂_c(x,y) + b; tick scalars computed once;
   < 10 ms/frame on the M1. Re-encode; fuse.
3. **Uncertainty-aware under-correction:** apply the posterior 25th percentile of dip magnitude
   (overshoot is worse — CLAHE amplifies both); shrink per pixel by λ = max(0, 1 − k·CV(x,y));
   **symmetric clamp on per-pixel change ±3%** (sign-agnostic — under forward scatter with a
   dark background the spot is *brighter* than the surround and must be darkened); ℓ_c ≥ 0;
   cosine taper to identity over 20 px at the mask edge.
4. **Fail-soft:** predictors outside the training hull (θ_⊙ below observed minimum, unvalidated
   gain, clipping, missing A) → multiplicative-M1-only or identity, logged. Identity on: model
   age > 14 days, gate-failure rate > 20% over 6 h, aim alarm, dew/rain changepoint, any NaN.
   Never touch non-sky pixels; never correct structure at scales > 300 px (vignetting
   explicitly out of scope).

## 7. Ops

- **Day 0:** ephemeris θ_⊙ scan (needs camera-pose calibration — pending); A_amp injection
  measurement; place control patches; deploy lock-safe ladder scheduler. (ganesha `/data`
  mount + rsync pipeline: pending — analysis starts local on the Air.)
- **Per tick (Air):** photometry + gates + crops (< 1 s).
- **Nightly (com.lsm.spotnightly, 03:40 local, ALL on the Air):** `spotfit.R` — Stan M2 fit
  per channel + M1-falsification regression (rlm fallback keeps the nightly record alive if
  the toolchain breaks); `spotmap.py` — per-pixel non-circular spot map from the crop stack;
  rsync backup of spot_lab/ + flatfield_acc/ + drift/science CSVs to
  ganesha:/mnt/anopotamia/spot_lab_pandr/ (backup only — no compute there; **rsync backlog
  is a first-class health metric**, logged in spot_lab/nightly.log). Changepoint scan (dew:
  t↓+s↑ broadly at dawn; cleaning: step t↑) reads the nightly t time series once ≥3 days
  exist. Remote check-in from anywhere:
  `ssh -J simon@ganesha.lan.cmu.edu -p 2202 proofsandreasons@localhost`
  then `tail ~/monitor/spot_lab/nightly.log` + `cat ~/monitor/spot_lab/fits/<date>/summary.csv`.
- **Weekly:** M4 refit; detection stacks; checkpoint memo (condition-bin residual table, t
  drift, LUT stability); disk audit against §2 caps.
- **Paths:** `spot_lab/{crops/,brackets/,ladders/,stacks/,fits/}`, `spot_photometry.csv`,
  versioned `tone_curve.json`, `spot_model.json` (valid-from stamps).
- **Timeline:** days 1–3 tone-curve validation + M1/M2 IRLS; days 4–14 hierarchy + model
  selection; days 10–21 shadow mode (corrected display frames computed in parallel, residual
  measured daily); ≥ day 17 re-enable review, including a blind visual A/B on the 10
  worst-θ_⊙ frames (no rings).
- **Post-enable watchdog:** measure residual dip on *served* frames every tick; auto-disable +
  alert on |residual| > 0.5% for 3 consecutive ticks or any changepoint (the blemish physically
  changed; the posterior is stale). 7 days of A/B crop archiving after the flip for eyes-on
  review.
- **Watch list:** dew (dawn t/s excursion), rain droplets (transient "spots" — registry
  quarantine, never fit), cleaning (epoch split), ISP adaptivity (LUT drift), sharpening halos,
  dusk quantization (inflate σ; never correct below the SNR floor).
