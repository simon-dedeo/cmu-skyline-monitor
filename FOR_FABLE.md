# Review of the spot-removal work

Date: 2026-07-28

## Bottom line

The project contains a good empirical idea: estimate a small, moving attenuation footprint from raw exposure brackets, correct each raw channel before Mertens fusion/CLAHE, and validate the result in the display domain. The retained sweep supports the central offline result: under the implicit selection rules used for Table 2 of `SPOT_FIT.pdf`, the median displayed dip falls from 4.465% to 0.524% over 495 frames, with median fractional removal of 88.08%.

The main problem is that `SPOT_FIT.pdf` currently reads as a description of a deployed, unified system, while this directory contains several incompatible generations of that system and does not contain much of the code needed to support the paper's strongest claims. In particular, the checked-in live corrector has no persistent template lock, no data-epoch-aware velocity placement, no map warp, no staleness policy, and no local-sky cap on its primary transmittance-map path. The checked-in spatial nightly estimator is a centroid/stack method, not the Bayesian pixel model described in the paper. A separate MAP result exists as derived images and JSON, but its fitting code and raw-to-result recipe are absent.

Unless important production code is missing from this snapshot, I would not circulate the paper as an account of the current implementation. I would either:

1. bring the actual `akdeniz` refiner/publisher and current `pandr` corrector into this directory, wire and test the described architecture, then revise the paper against that code; or
2. relabel the paper clearly as a design/prototype report and distinguish deployed, offline-prototype, and proposed components throughout.

## What I reviewed

- `spot_paper/SPOT_FIT.pdf` and `spot_paper/SPOT_FIT.tex`, including a visual render of all nine pages.
- The active-looking capture tree under `monitor-pandr/`, especially `flatfield.py`, `hdrfuse.py`, `skyshot.sh`, `spotlab.py`, `spotmap.py`, `spotfit.R`, `spot_m2.stan`, and `nightly.sh`.
- The newer root-level `spotnight.py` prototype.
- `SPOT.md`, `COME_BACK_REVIEW.md`, and the retained results under `spot_fit_check/`.
- Python syntax compilation for the relevant Python files.

There is no `.git` repository at this directory level, so I could not establish commits, branches, deployment ancestry, or whether a file is tracked. The remote `akdeniz` refiner described in the prose is not present. Conclusions below are therefore about the supplied directory, not unverifiable remote state.

## What is already strong

- Correcting the raw brackets before fusion and CLAHE is the right pipeline location. `monitor-pandr/hdrfuse.py:55-63` does this, and the paper explains why.
- An empirical per-pixel, per-channel map is preferable to using a Gaussian for correction. A Gaussian is useful for location, but it cannot reproduce the measured wings.
- The display-space closed-loop gain experiment is sensible for offline evaluation because it measures the artifact where viewers see it.
- The project has repeatedly identified real operational failure modes: selecting flat overcast frames, fitting a zero-amplitude blob, using a stale center, confusing the data epoch with run time, and evaluating a dip on cloud structure. Those lessons are valuable.
- Raw measurement is kept separate from corrected display output in `skyshot.sh:92-120` and `capture.sh:137-151`.
- The offline table is not invented. I reproduced it from `spot_fit_check/sweep2.csv` as described below.
- The paper is generally readable and the before/after example is persuasive. Its limitations section acknowledges the shared-center validation problem and frame-edge risk.

## Critical discrepancies between the paper and the supplied code

### 1. The paper conflates at least three different systems

The directory contains:

- the older deployed-looking `monitor-pandr` path, in which `nightly.sh` runs `spotfit.R` and `spotmap.py`, while `flatfield.py` consumes `spot_model.json` and `spot_tmap.npy` produced elsewhere;
- a newer root-level `spotnight.py` offline/nightly prototype, which writes `spot_state/spot_params.json` plus per-bracket maps and explicitly says that publishing “does NOT itself wire `flatfield.py`” (`spotnight.py:685-692`); and
- a separate MAP spatial analysis represented by `spot_fit_check/summary.json` and rendered panels, but without the fitting script that produced them.

The paper treats these as one coherent deployed code path. They are not one code path in this snapshot. The first improvement should be an explicit implementation-status table in the paper and a single authoritative runtime in the repository.

### 2. The described Bayesian spatial estimator is not checked in

The paper specifies a pixelwise Student-t blob model and says it is fit by multi-start CmdStan L-BFGS (`SPOT_FIT.tex:79-118`). The supplied Stan model, `monitor-pandr/spot_m2.stan`, contains only the scalar photometric regression `Lsp = t * Lbg + s`. `spotfit.R:47-60` samples that scalar model with four NUTS chains; it does not optimize the spatial model. `spotnight.py:276-314` estimates position from median maps and half-depth centroids, not from the stated likelihood.

The MAP panels and `summary.json` show that another analysis existed, but `spot_fit_check/gen.py` only turns already-derived JSON and PNGs into HTML. It cannot regenerate the fit.

Suggested change: add the actual spatial Stan/model-building and figure-generation code, its exact inputs, seeds, bounds, priors, and command line. If that code cannot be added, rewrite Section 2.2 as an offline analysis and do not imply that it is the nightly estimator.

### 3. “Honest epochs” are described but not implemented in `spotnight.py`

The paper emphasizes that every estimate carries the mean epoch of its input frames and that velocity is extrapolated from that epoch (`SPOT_FIT.tex:120-134`). `spotnight.py` stores `day`, `cx`, `cy`, and `prev`, but no data epoch. Its velocity is simply the difference between the last two accepted/retained centers (`spotnight.py:551-568`) and assumes one equally spaced step. It does not compute elapsed fractional days from frame timestamps.

This matters after skipped nights, delayed jobs, replay, partial-day data, or a change in the two-day frame mix. It also means the paper's most emphatic operational claim is not supported by the supplied estimator.

Suggested change: persist `data_epoch_utc`, `fit_window_start/end`, velocity in px/day, and its uncertainty. Extrapolate using actual elapsed seconds. Test multi-day gaps and irregular frame coverage.

### 4. The three-layer live tracker is absent

The paper says the live corrector uses posterior placement, velocity extrapolation, and a persistent normalized-cross-correlation template lock saved in `.spot_lock.json` (`SPOT_FIT.tex:203-226`). No such lock file, match-template call, warp, or persistence logic exists in the supplied runtime. Repository-wide search finds `.spot_lock.json` only in the TeX document.

`monitor-pandr/flatfield.py:107-132` applies the map at the fixed published `(x0, y0)` slice. It does not move the map based on time or frame content. `spotnight.py` also has no template lock; its `Corrector` places a fixed full-frame shape at a nightly center (`spotnight.py:350-398`).

Suggested change: either implement and test the tracker before describing it as live, or mark it as proposed. The lock needs an ambiguity test (best-vs-second-best peak), a surrounding-ring gate, freshness and displacement bounds, atomic persistence, and replay tests with injected motion and cloud structure.

### 5. The primary live correction does not have the safety cap claimed in the paper

The paper says every corrected pixel is clamped not to exceed the local sky fit and concludes that an inverse bright spot is structurally impossible (`SPOT_FIT.tex:244-247`). That cap exists in the fallback path (`flatfield.py:186-199`) and in the offline `spotnight.py` corrector (`spotnight.py:388-397`), but not in the primary transmittance-map path (`flatfield.py:107-132`). The primary path divides by the map, converts back to sRGB, clips only to `[0,255]`, and returns immediately.

Brightening-only division does not by itself prevent an inverse spot. An over-deep map, wrong amplitude scale, stale registration, or mismatched artifact can brighten above the true background.

Suggested change: compute the same local per-channel quadratic background used by the gate and apply `corrected = min(I / T, B)` in linear light on the primary path. Add a small tolerance for fit noise if necessary, and measure rather than assert the resulting overshoot bound.

### 6. Exposure scaling and self-calibration are not combined as described

The paper says the map path applies both an exposure/level curve and gated per-tick amplitude trimming (`SPOT_FIT.tex:228-242`). In `flatfield.py`, the primary map path can apply `scurve` scaling (`flatfield.py:123-130`) but returns before the fallback self-calibration code. The fallback can self-calibrate, but it does not use the empirical map. The claimed two-mechanism combination is therefore absent from this code.

Suggested change: factor placement, scale prediction, gated scale measurement, and map application into one primary routine. Log predicted scale, measured scale, gate reason, chosen scale, correlation, center, model age, and residual for every corrected bracket.

### 7. The nightly and live artifact interfaces do not match

The paper names `spot_model.json + spot_tmap.npy`. `flatfield.py` reads those files. `spotnight.py` writes `spot_state/spot_params.json` and a directory of `br0_b.npy`, `br0_g.npy`, etc. `spotmap.py` writes still another set of dated `spotmap_b.npy`, `spotmap_g.npy`, and `spotmap_r.npy` files. `nightly.sh` never turns its maps into the files consumed by `flatfield.py`.

This is more than naming: the files encode different models. `spotnight.py` builds separate encoded-space maps for each bracket role; `spotmap.py` builds inverse-sRGB-linearized maps; the live file may contain a reference map plus a level curve from the absent refiner.

Suggested change: define one versioned bundle schema and one producer/consumer contract. A manifest should include schema version, code version, generated time, data epoch, center and velocity, crop origin and content centroid, map color order and domain (encoded or linear), exposure/gain applicability, training days, validation metrics, and hashes of all arrays. Publish the bundle atomically; reject mismatched, non-finite, implausible, or stale bundles locally.

### 8. The checked-in `spotmap.py` has a frame-edge coordinate error

`spotlab.py` seeds the spot at full-frame `(1163,187)` and creates a 448-pixel crop with `y0=max(0,187-224)=0` (`spotlab.py:22-27,117-120`). The spot is therefore at local crop coordinate `y=187`, not `y=224`. `spotmap.py:16-18` nevertheless hard-codes `CX=CY=224` and centers its background annulus there. This contradicts the paper's edge-aware, blemish-local geometry and can bias both background and map shape.

The same script also lacks the stated bright-then-flat selection, per-frame motion registration, and MAD clipping (`spotmap.py:34-57`).

Suggested change: save crop origin and spot-local coordinates alongside every crop, or derive them from full-frame coordinates. Never infer that a clamped crop is centered. Add a regression test for a spot closer to an edge than half the crop width.

### 9. Staleness and failure handling are largely narrative

The paper claims tiered staleness, stand-down after seven days, artifact-based success checks, carry-forward, and escalation (`SPOT_FIT.tex:417-433`). The deployed-looking `nightly.sh` logs success from process exit codes (`nightly.sh:16-17`), while both analysis programs deliberately exit zero for insufficient data or caught errors. The live `flatfield.py` has no model-age check. `spotnight.py` has better state and a completion marker, but it is not scheduled or wired to the live consumer in this snapshot, and it logs escalation without an alert transport.

Suggested change: make the local consumer enforce age and health independently of the remote producer. Add explicit states such as `fresh`, `prediction_only`, `gated_fallback`, and `identity`. Assert expected artifacts, hashes, shapes, and metrics before promotion. Send a real alert after repeated rejection.

### 10. Smaller inconsistencies will make unattended operation harder

- `spotnight.py:8-11` advertises a `--shadow` command-line option, but its argument parser has only `--publish` (`spotnight.py:685-693`). Shadow is the default behavior, but the documented command fails.
- `monitor-pandr/nightly.sh:2-7` says the work runs entirely on the Air, while the paper says the entire nightly cycle runs on `akdeniz`. This may reflect an undeclared replacement, but an operator cannot tell which schedule is authoritative.
- `monitor-pandr/capture.sh:12-14` still says there is no HDR/exposure sweep, while the operative code and later comments use a three-frame HDR bracket.
- Absolute interpreter and host paths are scattered through the scripts. There is no checked-in deployment configuration or environment validation tying this directory to the actual machines.
- There is no automated test directory for the correction, tracking, artifact schema, or replay behavior. The Python files compile, but syntax success does not exercise any of the scientific or safety properties.

Suggested change: add one short operational README generated from the actual launch configuration, remove stale comments/options, and run unit plus replay tests in a repeatable local command.

## Statistical and evidentiary audit

### The 495-frame offline table is reproducible, but the filters need to be disclosed

From `spot_fit_check/sweep2.csv`, the paper's first row is reproduced exactly by:

```text
lvl >= 140
1% <= before <= 9%
```

This gives:

| quantity | reproduced value |
|---|---:|
| n | 495 |
| median before | 4.465% |
| median after | 0.524% |
| median of `1 - after/before` | 88.077% |
| `abs(after) > 1.5%` | 8.283% |
| `after < -1%` | 1 frame |
| minimum after | -1.278% |
| median solved gain | 2.825 |

Adding `ring_struct < 3` gives `n=301`, median after `0.513%`, and `6.977%` above 1.5%, matching the second row.

These definitions do not appear in the paper. “Clean daylight” must state the level threshold, the requirement that the uncorrected measured dip lie in `[1%,9%]`, and whether those thresholds were chosen before viewing the result. The latter condition selects on the outcome-like measurement and excludes negative, weak, and cloud-inflated cases; that may be appropriate for estimating performance on a detectable blemish, but it is not a generic daylight population.

Also define “inverse bright spot.” There are 66 negative residuals in the 495-frame subset, six below -0.5%, and one below -1%. The phrase “at most one inverse bright spot” silently uses a roughly -1% visibility threshold. State that threshold.

### Report day-level uncertainty, not only frame counts

The 495 frames come from a short, autocorrelated time series. Neighboring frames share weather, exposure plan, model, position error, and map. Treating 495 frames as independent exaggerates precision. Report the number of days and ticks per day, per-day medians, a day-block bootstrap interval, and leave-whole-days-out evaluation.

For a genuinely out-of-sample replay, build the map and tune thresholds using days strictly before the evaluation day. `spotnight.py:459-489` can fall back to validation frames from the same prior days used to build the map when the nominal run day lacks 20 clean ticks, so the current gate is not guaranteed to be out of sample.

### The live 96-99% claim is not reproducible from this directory

The paper gives no sample size, timestamps, frame list, CSV, or script for the claimed live residual range of 0.03-0.7% and 96-99% removal (`SPOT_FIT.tex:472-479`). The retained `result.json` supports the displayed Figure 4 example, 4.49% to 0.597%, which is about 86.7% removal, not the headline range. Later live improvements may be real, but their evidence is not retained here.

Suggested change: add a machine-readable paired-live table with frame IDs, raw bracket paths or hashes, model bundle ID, predicted/measured centers, before/after dips, selection flags, and code version. State `n`, date range, median, quantiles, worst case, and exclusions.

### The additive-component inference is exploratory, not established

The paper reports extremely small p-values and says the effect is stable across nights (`SPOT_FIT.tex:275-284`). The checked-in falsification is a simple `lm(dip_lin ~ 1/Lbg)` (`spotfit.R:43-46`) applied to repeated frames. It does not model within-tick/day dependence, errors in the fitted background, day effects, or response-curve uncertainty. The underlying photometry aperture is fixed at `(1163,187)` (`spotlab.py:21-24`) even though the paper places the current blemish near `(1300,132)` and admits the aperture drifted off it.

Inverse sRGB is explicitly only a hypothesis (`spotlab.py:20,34-36`), and a camera/ISP tone curve error can generate the apparent intercept. The numerical sign may be interesting, but “not purely multiplicative” and “Rayleigh-like” are too strong.

Suggested change: track the aperture with the independently estimated center; calibrate the response curve per channel and relevant gain; use an errors-in-variables or joint pixel model; block or cluster uncertainty by tick/day; and show held-day stability. Until then, call the additive term a response-curve-confounded residual.

### The spatial model needs a complete specification

Equation 2 does not define the components of `Q`, their parameterization/priors, all bounds, or whether `A_{k(m)}` is per bracket role, per stack, or per exposure observation. The prose says depth is free per exposure, but the index shown can be read as one amplitude per bracket class. The random-walk equation does not include the stated drift prediction. “Posterior” is also used for a MAP point estimate even though the paper says the posterior is not sampled.

Suggested change: include the complete generative model, exact priors and constraints, indexing, data normalization, optimizer settings, initialization, and a diagram of training/evaluation splits. Call the output a MAP state (with a Laplace covariance if computed), not a posterior.

## Physical interpretation

The correction method need not know what the object is, and the paper should keep that useful separation. The physical account is presently more certain than the evidence permits.

- The abstract and Section 5 say the blemish itself migrates on or in the glass (`SPOT_FIT.tex:30-35,261-273`). Section 9 later says the least exotic explanation is a static exterior speck plus camera/window motion (`SPOT_FIT.tex:526-539`). The later account is more cautious, but the abstract was not updated.
- Phase correlation on distant buildings measures apparent scene motion; it cannot distinguish a near object moving from camera translation relative to a static near object. The paper eventually acknowledges exactly this degeneracy.
- “The object is not on the window” is too categorical. The inference depends on assumed entrance-pupil diameter, camera-to-pane distance, effective angular pixel scale, the definition of the measured 76-pixel width, and an idealized opaque-disk model. None has a measurement uncertainty in the document.
- The claims of grey extinction and chromatic defocus depend on registered maps, response linearization, white balance/ISP stability, and a fit-generation path that is not retained.

Suggested change: retitle Section 9 “Physical hypotheses and discriminating tests.” Present the exterior speck as the leading hypothesis, propagate ranges from measured optical parameters, and move the cheap fiducial/press/inspection tests ahead of detailed identification. Update the abstract to “the apparent blemish position moves” until the fiducial test distinguishes object motion from camera/window motion.

## PDF and presentation audit

- Figure 3 is visibly clipped beyond the right page edge on page 7; much of the `akdeniz` lane is cut off. The LaTeX log reports a 76.9 pt overfull box at the figure/caption block (`SPOT_FIT.log`). Scale the TikZ picture to `\textwidth`, redesign it vertically, or split it into two panels.
- The architecture table produces another 29.1 pt overfull box. Use `tabularx`, shorter labels, or a two-line cost column.
- The PDF metadata has blank title, author, subject, and keywords and the document is untagged. Set `pdftitle`, `pdfauthor`, and related `hyperref` fields; tagged PDF would improve accessibility if the toolchain supports it.
- There are no references, code/data availability section, versioned artifact IDs, or reproducibility commands. For a technical note making empirical and optical claims, these are essential.
- The abstract presents the most speculative and least reproducible claims as settled. It should lead with the observed artifact, what is deployed versus experimental, the evaluated population, and the principal limitation.
- The flow diagram is attractive in concept but too dense for the page. It will be clearer after the implementation is reduced to one authoritative path.

## Recommended implementation plan

### Priority 0: make the description truthful and the live path safe

1. Choose the authoritative producer and consumer. Either wire `spotnight.py` into `flatfield.py`, or retire it in favor of the actual `akdeniz` refiner. Bring any remote-only source into version control.
2. Define and atomically publish one model bundle with schema/version/hash/epoch metadata. Reject mismatches and NaNs locally.
3. Add the local-sky overshoot cap to the primary empirical-map path.
4. Enforce model age and health in `flatfield.py`; stale or invalid state should degrade to identity unless a validated fallback is truly current.
5. Remove or update the old hard-coded seed `(1163,187)`. At the paper's current center `(1300,132)`, that seed is about 148 px away. A once-good fixed seed is not a safe months-long fallback for a migrating feature.
6. Fix clamped-crop coordinates in `spotmap.py` and add an edge-geometry regression test.
7. Revise the paper now to label absent features as proposed. Do not wait for all engineering work before correcting the status claims.

### Priority 1: unify tracking and validation

1. Persist exact data epochs and velocity in px/day, with uncertainty and irregular-gap tests.
2. Register every map-building frame at its timestamped/interpolated center; record the map content centroid separately from crop origin.
3. Implement the template lock if it remains part of the design. Save diagnostic correlation surfaces for replayed failures.
4. Combine exposure prediction and gated self-calibration in the empirical-map path.
5. Add a per-tick served-residual watchdog and auto-disable policy. Keep uncorrected raw brackets immutable.
6. Ensure training and acceptance days are disjoint. Report day-blocked performance and tail risk.

### Priority 2: make the science reproducible

1. Check in the spatial MAP fitting script and every figure/table generator.
2. Calibrate the camera/ISP response rather than assuming inverse sRGB, including gain-dependent behavior.
3. Track the photometric aperture instead of leaving it at the original seed.
4. Perform the fiducial and physical inspection tests before asserting object identity or motion mechanism.
5. Add a data manifest containing paths/hashes for every frame used in published numbers.

## Minimum test suite I would require

- Synthetic transmittance injected into uniform sky, quadratic gradients, and real cloud crops; recover center, shape, amplitude, and residual.
- Registration errors from 0 to at least 60 px, including multi-day gaps and acceleration.
- Top-edge crops where the spot is above the crop midpoint and the annulus is clipped.
- Per-channel and per-bracket response changes, clipping, low light, and gain changes.
- Bad artifacts: stale bundle, mismatched JSON/array versions, wrong shape/color order, NaN/Inf, all-ones map, over-deep map, and truncated file.
- Template-lock distractors: cloud edge, contrail, roofline, and two comparable correlation peaks.
- Assertions that no primary-path corrected pixel exceeds the fitted local sky by more than a declared tolerance.
- Leave-one-day-out replay with frozen thresholds and an independently located evaluation aperture.
- End-to-end test from three raw brackets through correction, Mertens fusion, enhancement, measurement, logging, and failover.

## Suggested framing for the next paper revision

A clearer document would have four explicit layers:

1. **Observed problem and data**: camera, raw brackets, apparent motion, edge geometry.
2. **Deployed correction as of a named version**: only code actually running on `pandr`, with its exact artifact contract and safety behavior.
3. **Offline/nightly estimator and validation**: the actual fitting algorithm, replay split, and reproducible table.
4. **Proposed tracking and physical hypotheses**: template lock or other features not yet deployed, plus discriminating physical tests.

The central result is worth preserving, but it should be stated narrowly: on a disclosed, detectable-dip daylight subset, an offline display-closed correction reduced the median measured dip from 4.465% to 0.524%. That is already a useful result. The autonomous-live and physical-identification claims should become equally strong only after their code and evidence are retained alongside it.
