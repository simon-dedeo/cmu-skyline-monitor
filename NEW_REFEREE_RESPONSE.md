# Referee report: *Pittsburgh in a Grain of Sand*

Manuscript reviewed: `spot_paper/SPOT_FIT.pdf`, v10, dated 2026-07-28  
Recommendation: **Reject in the present form; invite resubmission after new validation**

## Confidential comments to the editor

This is an inventive and unusually candid case study. The authors recognized a moving low-frequency camera blemish, built an empirical transmittance-map correction, and documented many operational lessons that are genuinely useful. The manuscript is visually polished, the core image-processing idea is sensible, and the authors disclose several limitations that are too often hidden: selection on a detectable dip, negative residuals, day-level dependence, the distinction between deployed and offline analyses, and the weakness of the present cloudy-day live evidence.

I nevertheless cannot recommend publication in its current form. The article combines three papers—a single-camera correction system, a physical identification of one speck, and a broad particle-optics/automotive argument—and states the least established parts most strongly. Several central claims are contradicted by the checked-in implementation or cannot be reconstructed from the deposited evidence. In particular:

1. the purported live evidence is a retrospective analysis whose rows do not preserve the model actually used at each tick;
2. the deployed nightly estimator is a centroid heuristic, not the Bayesian MAP estimator described in the architecture and abstract;
3. the physical identification, “strain gauge,” and extinction/scattering conclusions depend on unmeasured entrance-pupil geometry, an undocumented direct inspection, and no fixed fiducial or controlled removal test;
4. the numerical optical account contains internal inconsistencies; and
5. the raw data and end-to-end analysis needed to reproduce the paper are absent.

These are not cosmetic defects. A credible revision needs prospective test data, direct optical calibration, and a versioned reproducibility package. If the journal permits a major revision that includes substantial new experiments, that would be another reasonable editorial label; in ordinary usage, however, I would reject and invite a focused resubmission.

## Comments to the authors

### Overall assessment

The strongest publishable contribution is narrower than the current manuscript: a defect-local, per-channel transmittance map, registered at a tracked apparent position and applied before nonlinear exposure fusion, can substantially reduce a small near-field blemish in a fixed sky camera. The retained `sweep2.csv` does reproduce the stated selected-subset medians, and the before/after examples make the practical value clear.

The paper should be rebuilt around that defensible result. The physical story is an interesting hypothesis and may become a second contribution after controlled calibration. It is not yet an established “single-particle optics experiment,” an optical strain gauge, an independent confirmation of Mars-rover work, or a physics-first alternative for autonomous vehicles.

### Strengths

- The defect-local multiplicative correction is well motivated, and applying it to raw brackets before Mertens fusion/CLAHE is the correct architectural location.
- The manuscript clearly distinguishes empirical map shape from the Gaussian model used for localization.
- The selection rule for the 495-frame table, negative-residual counts, per-day range, and day-block bootstrap are disclosed.
- The deployed/offline/proposed distinction is much improved over the earlier project note.
- Fail-soft loading, bounded placement, map/model hashing, and refusing weak maps are worthwhile engineering ideas.
- The PDF is readable and professionally laid out; I found no substantive clipping or broken references in the rendered 17 pages.

## Major comments

### 1. The “live” validation does not retain live provenance

Section 8 calls `spot_paper/evidence/live_paired.csv` machine-readable live evidence with model-version provenance. The generating script, `spot_paper/code/live_evidence.py`, instead loads one current `spot_model.json` at analysis time, uses that model to predict the center for every historical row, and writes the same `mm["updated"]` value into every row. All 90 retained rows consequently have the same later model timestamp (`2026-07-28T17:19:14+00:00`), including rows from 07-27. The CSV contains neither the bundle hash that served each frame nor the code revision and enable-state in effect at that tick.

This matters because `PRODUCTION_STATUS.md` says that archive frames were reprocessed and installed. The compared “served” archive image can therefore be a backfilled output rather than the immutable output served at capture time. The 07-27 and 07-28 results are useful retrospective paired-reprocessing measurements, but the deposited material does not establish them as contemporaneous live performance.

Required revision:

- Relabel the present CSV and results as retrospective unless immutable logs prove otherwise.
- Run a prospective trial with append-only per-tick records containing raw bracket IDs and hashes, served-image hash, timestamp, exact model/map hash, code version or commit, correction-enable state, predicted and locked centers, lock score and ambiguity margin, amplitude scale, and any fallback path.
- Freeze the model before evaluation and collect multiple complete clear, mixed, and cloudy days. Do not revise the chain midway through a test day and then pool the results.
- Report missing ticks and failures, not only accepted clean ticks.

### 2. The manuscript and the production code describe different estimators

Section 2 correctly says that `production/akdeniz/spotnight.py` uses median ratio maps and a half-depth centroid, while the Bayesian pixel fit is offline. The abstract nevertheless calls the deployed chain an “autonomous Bayesian pipeline,” and Table 2/Section 6 says a multi-start L-BFGS MAP position/shape fit runs nightly. That MAP fit is not in the deployed nightly path.

The checked-in statistical implementations also do not form one reproducible model:

- `spotell.stan` most closely matches the printed elliptical equation, but declares `A` with length `M` and then indexes it by `bkt_of[m]`, leaving amplitudes above `K` unused. Its header says depth is free per day-by-bracket map, while the likelihood gives one amplitude per bracket class.
- `spothier.stan` is circular, permits amplitude down to zero, and has different bounds and priors from the printed model.
- `spothier.R` calls NUTS sampling and expects a data layout inconsistent with the checked-in `spothier.stan`; the manuscript instead says the posterior is never sampled and that optimization uses multiple-start L-BFGS.
- No checked-in runner contains the claimed optimize/multi-start commands or recorded seeds.

Required revision:

- Choose one model and make the equation, indexing, bounds, priors, inference method, and claimed result correspond exactly to it.
- Supply one executable driver and a small retained input fixture that reproduces the track and the quoted 3-pixel agreement.
- Call the deployed tracker a recursive, gated centroid tracker unless it actually runs the Bayesian model.
- If only a MAP estimate is retained, avoid “posterior” terminology for the nightly state and do not imply Bayesian uncertainty quantification.

### 3. A velocity-unit bug remains in the published live path

`spotnight.py` now computes center velocity using the elapsed time between `fit_epoch` and `prev_epoch`, as the paper says. `production/akdeniz/publish_live.py`, however, recomputes the published velocity as

```python
vx, vy = float(cx - prev[0]), float(cy - prev[1])
```

without dividing by the elapsed epoch interval. The resulting value is then interpreted as pixels per day both while registering map frames and in `flatfield.py`. A skipped night, delayed fit, replay, or non-unit interval therefore corrupts the velocity even though the state contains the information needed to avoid it. This directly contradicts the claim that the published velocity is robust to gaps and replays.

The publisher should use a state velocity already expressed in px/day or divide by `fit_epoch - prev_epoch`, with explicit unit tests covering irregular intervals. The bundle should carry units and the two epochs from which velocity was derived.

### 4. Other implementation claims need a line-by-line reconciliation

Several smaller mismatches collectively weaken the system account:

- The paper says template locking requires a clean surrounding ring. The primary lock in `flatfield.py` is gated by correlation, runner-up margin, and displacement; ring cleanliness gates amplitude self-calibration, not the lock.
- The paper says schema version is checked on load. `_load_tmap()` checks a hash when present and finiteness, but does not reject an unsupported schema value.
- On a torn or corrupt bundle, the old map is not retained in memory as stated; the per-frame loader rejects the primary path and falls through to the ring-fit fallback.
- The corrector is controlled by an external `.spot_apply_on` flag, while the checked-in header says it ships off. The snapshot does not preserve or log the flag state, so “deployed” is not by itself evidence that correction was enabled.
- `PRODUCTION_STATUS.md` still lists a “local-sky cap” although the same file and the manuscript say the scene-referenced cap was removed.
- The checked-in `spot_model.json` reports an acceptance gain of 3.323, which cannot have been produced under the current stated `[0.4, 1.8]` gain clamp. This suggests that code and artifact are from different revisions or that “gain” has undocumented semantics.

Add a claim-to-code table, tag one immutable release, and generate the paper from that release. Operational chronology belongs in a supplement, not in contradictory comments spread across rapidly revised files.

### 5. The offline performance estimate is selected, adaptive, and not out of sample

I independently recover the headline row from `spot_fit_check/sweep2.csv` under the disclosed filters: level at least 140 and measured uncorrected dip between 1% and 9% gives 495 frames with median dip approximately 4.46% before and 0.52% after. Adding `ring_struct < 3` gives 301 frames and the stated residual rate. This is a welcome reproducibility point.

It is not yet a general performance test. Selection uses the measured target defect, the same short eight-day period informs localization/maps and evaluation, and the offline closed-loop procedure chooses a gain by evaluating the corrected residual on the test frame itself. That is a useful oracle-style upper bound for archive restoration, but it is not a prospective prediction of performance on a new frame. Neighboring ticks are strongly dependent, and a block bootstrap over only eight days cannot establish transportability.

Required revision:

- Separate map construction, parameter tuning, acceptance-threshold selection, and evaluation by complete days.
- Include leave-one-day-out and, preferably, a later untouched prospective period.
- Evaluate the locked live algorithm without per-frame outcome-driven gain search as the main result; report closed-loop archive restoration separately.
- Report the unconditional daylight population alongside prespecified visibility/weather strata, including skipped corrections, inverse spots, worst cases, and failure duration.
- Compare against meaningful baselines: fixed map/fixed center, velocity-only placement, template lock without amplitude self-calibration, and a simple local-flat-field method.
- Use day- or weather-block uncertainty and show per-day values rather than treating hundreds of ticks as independent replication.

### 6. The physical identification is plausible but not identified by the present data

The claim of a roughly 0.25-mm opaque speck on the window is plausible and is consistent with the geometric model in Willson et al. The official camera specification also supports `f/4.0`, fixed focus, and the advertised 30–120 cm 4K focus range. Those facts do not provide the missing ground truth.

The manuscript infers actual focal length from “21 mm equivalent,” sensor format, and field of view; infers entrance-pupil diameter from that focal length and f-number; infers speck size from shadow depth; and then infers pupil-to-pane distance from shadow width. The solve assumes an opaque disk, a uniformly filled circular pupil, calibrated linear radiometry, a particular width definition, and known angular scale. It does not independently measure the entrance pupil’s diameter or axial position. Further, a focus *range* is not a fixed focus distance, and the factor `1-d/F` should use the scene-object geometry; the sky/skyline is effectively at infinity, not at the webcam’s nominal focus setting.

The assertion “Nothing on the glass ever moved” is especially unsupported. The paper itself concedes that a fixed near-field fiducial is required to distinguish object motion from camera-to-pane translation. Without that fiducial, apparent motion can contain camera flexure, pane flexure, object motion, and estimator/map drift.

Required controlled measurements:

1. Photograph and measure the speck directly with a scale or microscope.
2. Wipe the predicted location while recording before/after frames; test inner and outer surfaces separately.
3. Place opaque calibration dots of known diameter on each pane surface and compare their depth/profile with the unknown feature.
4. Translate the camera by known sub-millimeter increments and independently measure pixels per millimeter.
5. Add a fixed fiducial so camera/pane motion and object motion are separable.
6. Measure or image the entrance pupil, or calibrate the effective pupil geometry by the known-dot experiment.

Until then, use “consistent with an approximately 0.25-mm window speck” and “motion consistent with camera–pane flexure,” not categorical identification.

### 7. The stated 92 px/mm lever is not derived and appears inconsistent with the other numbers

For a 3840×2160 image with a 90-degree diagonal field of view, the focal length in pixel units is approximately

```text
sqrt(3840^2 + 2160^2) / (2 tan 45°) = 2203 px.
```

At the manuscript’s inferred 30–35 mm pupil-to-pane distance, a near-field translational lever is therefore approximately 63–73 px/mm, not 92 px/mm. If 90 degrees is interpreted as horizontal field of view, the value is smaller still (about 55–64 px/mm). Lens distortion or crop modes may change this calculation, but then the local angular calibration must be measured and reported. The 120-pixel track would correspond to roughly 1.6–2.2 mm over these simple calibrations, not the categorical 1.3 mm.

This discrepancy affects the abstract, Sections 5 and 9–10, and the claimed “strain gauge.” Give the full derivation with a calibrated local pixels-per-radian value and uncertainty, and validate it by controlled camera translation.

### 8. The extinction and chromatic-scattering claims are substantially overinterpreted

The paper moves from a useful geometric dust-shadow model to a much stronger extinction-paradox experiment without sufficient experimental control.

First, the internal notation is inconsistent. Section 9 uses an acceptance angle `a/d ≈ 34 mrad`, while Section 10 correctly calls roughly 18 mrad the half-angle. The forward-lobe scale alternates between `lambda/s` and `lambda/(2s)`. The edge-diffraction Fresnel number is said to be about 4 in Section 9 but is calculated as 0.85–1.13 in Section 10; with radius `s/2`, the latter is the consistent calculation.

Second, `Q_ext = 2` is a far-field plane-wave cross-section statement. A local image dip behind a finite pupil in the near field, with forward-scattered light recollected, is not expected simply to be twice the geometric core depth. Thus the measured 4.5–5.4% shadow does not “exclude” physical extinction efficiency 2; it shows that this imaging system registers an effective attenuation near geometric blockage under the assumed size and pupil geometry. Markel’s approximately 60-degree detector result concerns a particular operational extinction measurement and does not by itself license the manuscript’s comparison between that detector aperture and this near-field imaging pupil.

Third, the RGB argument is not a decisive out-of-sample test. Blue and red determine two quantities and green supplies one approximate check, but no uncertainty is reported. The sensor bands are broad and coupled by demosaicing, white balance, color matrices, sharpening, compression, and an assumed inverse-sRGB response. `color.py` computes the “integrated” deficit only inside `r < 90` and clips negative map values to zero. Both finite truncation and one-sided clipping can bias integrals upward and can affect a broader channel differently. The green value 1.08 versus prediction 1.045 cannot be interpreted without map-level uncertainty and processing calibration.

Required revision:

- Correct the angle, lobe-width, and Fresnel-number definitions throughout.
- Recast the geometric-depth agreement as an effective finite-aperture attenuation result unless a wave-optics forward model is fit.
- Supply a scalar/vector diffraction or validated numerical model that includes the measured pupil, particle size/shape, standoff, wavelengths, and sensor integration.
- Bootstrap maps by day/frame and report uncertainty on depth, width, and signed full-support integrals; demonstrate convergence with integration radius and do not silently clip negative estimates.
- Use RAW sensor data or calibrate the camera response and color pipeline. Controlled narrow-band illumination would be far more persuasive.
- Remove claims that three uncalibrated bands exclude rust, sap, pollen, chlorophyll debris, and other materials.

The JPL dust paper supports the geometric convolution/area-ratio model and used approximately 0.25-mm particles with a 1.1-mm entrance pupil, but its validation compared artifact diameters; it explicitly notes that reference-particle variability prevented a quantitative predicted-versus-measured optical-density comparison. Calling the current work an “independent confirmation” of all depth and lever predictions is therefore too strong.

### 9. Reproducibility is not yet publication-grade

The repository contains derived CSVs and figures, but none of the `.npy` maps or raw bracket images needed by the analysis scripts. The program that generated `sweep2.csv` is not present. Most scripts hard-code host-specific `~/monitor`, `~/brackets`, or `/tmp/...` paths. There is no environment lockfile, data manifest, one-command workflow, or checked-in artifact containing the fitted parameter estimates behind all paper tables and figures.

The two files under `tests/` are diagnostic scripts rather than automated tests. The synthetic test creates a defect from the same map/scurve/warp assumptions that the correction uses, so a zero residual verifies algebraic sign and geometry but not model validity. It prints `FAIL` without a failing exit status or assertion. The dipole script is date- and home-directory-specific and the reported post-fix sample is only two frames.

Required revision:

- Deposit raw or losslessly cropped bracket data sufficient to reproduce every result, subject to a clear data license.
- Include all fitted maps, bundle hashes, model outputs, and a machine-readable figure/table manifest.
- Provide a container or locked environment and a single documented command that rebuilds the numerical results and manuscript.
- Turn the diagnostics into assertions with portable fixtures and nonzero failure exits.
- Add tests for irregular epochs, edge-clamped maps, bundle corruption, stale models, lock distractors, cloud edges, missing channels, accelerated motion, and unchanged pixels outside the footprint.

### 10. Safety and generalization claims exceed the experiment

The primary path can apply up to an 18% brightness increase (`T >= 0.85`) to a feature whose usual depth is about 5%. It has no scene-referenced cap, and the self-calibration uses the same frame it alters. This may be a reasonable design choice for this artistic sky monitor, but the paper lacks a false-correction study under clouds, contrails, birds, glare, saturation, and structured scene content. “Noise texture passes through untouched at every frequency” is also stronger than the evidence: spatially varying multiplication changes noise amplitude, and map warping/interpolation changes the correction field.

The automotive claim should be removed from the title and abstract. A fixed camera viewing mostly smooth sky through one pane, with one known apparent defect and no safety consequence, is not evidence for correcting diverse lens soiling in safety-critical driving. At most, automotive sensing is a motivation for future work. A credible automotive claim would require multiple contaminants, cameras, scenes, weather conditions, downstream perception metrics, uncertainty/failure detection, and a safety case showing when correction is preferable to abstention.

### 11. Novelty claims should be narrowed and documented

The manuscript says no prior work continuously tracks a single adherent occluder “migrating across the optic,” but its own preferred interpretation is that the grain never migrates and the camera/pane flexes. The operational novelty is tracking the *apparent image position* of a near-field blemish. State that directly.

Similarly, “first systematic per-channel measurement,” “independent terrestrial confirmation,” and “no antecedent” require either a documented systematic search strategy or softer wording. The current literature discussion is interesting but too polemical for the experimental support. The physical-mechanism debate around the extinction paradox is not resolved or materially discriminated by this dataset, as the manuscript partly acknowledges.

## Minor and editorial comments

1. Replace the machine names in the author line with human author(s), affiliations, a corresponding author, and contribution statements. An AI system cannot satisfy standard authorship criteria; disclose AI assistance according to the target journal’s policy and make the human authors responsible for all claims and citations.
2. Add a formal code/data availability statement, license, funding statement, and conflict-of-interest declaration.
3. Clarify whether the advertised 21 mm is full-frame-equivalent focal length. The f-number must be combined with actual focal length, not equivalent focal length, to infer pupil diameter.
4. Distinguish support diameter, FWHM, and half-depth diameter for the convolution of a finite particle and pupil. The expression `(s + a)/d` describes a support scale, not automatically the measured FWHM.
5. Report uncertainty for every physical quantity in the abstract, or remove precision that is not experimentally supported.
6. Define whether the advertised 90-degree field of view is horizontal, diagonal, and distortion-corrected; provide a local calibration at the blemish position.
7. “Live at proofsandreasons.io” is not archival evidence and will change after submission. Cite a frozen dataset instead.
8. The additive-term p-values should be removed unless temporal dependence, response calibration, and the drifting photometry aperture are addressed.
9. Report the exact number of training frames for each map and the overlap between map-building, gain-fitting, and evaluation data.
10. Give timing measurements with hardware, number of trials, and a distribution rather than a single 84 ms value.
11. Separate UTC capture times from EDT display times consistently.
12. Reduce autobiographical phrases such as “earned expensively,” “learned twice,” and “hard-won” in the research article; retain them, if desired, in a project narrative.
13. The paper is too broad for its evidence. Removing the particle-optics controversy and automotive sections would make the correction paper shorter and stronger.

## A practical route to a publishable resubmission

I see two viable papers, which could also be staged as one focused paper plus later follow-up.

### Route A: image-correction systems paper

Make the contribution the tracked empirical flat field. Freeze the implementation; repair the epoch bug; provide complete reproducibility; and run a prospective, day-separated evaluation with ablations, failure accounting, and immutable per-tick provenance. Treat the object as an unknown near-field blemish and the optics section as motivation only.

### Route B: calibrated optics case study

Add known-size dots, measured camera translations, direct pupil calibration, a fixed pane fiducial, controlled narrow-band or RAW measurements, and the wipe/localization intervention. Fit an explicit wave/geometric model with uncertainty. Only then test the particle-size, standoff, strain, extinction, and spectral-recollection hypotheses.

The current manuscript contains the seed of both papers. Publication requires deciding which claims are observations, which are model-dependent inferences, and which remain attractive conjectures—and then matching the prose to that hierarchy.

## Verification notes and primary sources consulted

- The official [Elgato Facecam 4K specifications](https://www.elgato.com/us/en/p/facecam-4k) support f/4.0, fixed focus, a 30–120 cm 4K focus range, a 1/1.8-inch sensor, 21 mm listed focal length, and 90-degree field of view. They do not directly give the actual focal length, entrance-pupil diameter, pupil axial position, or a unique fixed focus distance used in the manuscript’s solve.
- [Willson et al., 2005 (JPL PDF)](https://www-robotics.jpl.nasa.gov/media/documents/3_willson_11b.pdf) directly supports the geometric collection-cone, convolution, and area-ratio model. Its validation used roughly 0.25-, 0.5-, and 1-mm particles and a 1.1-mm pupil at f/22, but quantitatively compared artifact diameters rather than optical densities.
- [Markel, 2020 (author manuscript)](https://whale.seas.upenn.edu/vmarkel/EPUBS/JQSRT-2020-246-106933_AAM.pdf) emphasizes that operational extinction measurements depend on detector geometry and that large integrating apertures can cross over toward absorption. Its plane-wave/integrating-detector setup is not automatically equivalent to this near-field image-pupil geometry.

