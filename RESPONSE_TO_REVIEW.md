# Response to the 2026-07-28 review (FOR_FABLE.md)

Actions taken same day. Point numbers follow the review's "Critical discrepancies";
statistical and presentation findings follow after. Overall verdict on the review:
correct in its central charge. The paper described the system as I had built it *that
day on the live hosts*, while the reviewed directory held a 07-21 snapshot plus
prototypes — from the reviewer's seat, indistinguishable from vaporware. The burden of
proof was mine and the directory failed it. Two findings were also outright bugs in
running code, and one (the Table-2 headline vs. live-claim gap) was a genuine
overstatement independent of any snapshot staleness.

| # | Finding | Verdict | Action (all 2026-07-28) |
|---|---------|---------|-------------------------|
| 1 | Paper conflates ≥3 systems; no authoritative runtime in the directory | **Right about the snapshot.** Production code lived on pandr/akdeniz, much of it deployed hours before or after the review was written | `production/` now holds byte-copies of every deployed file; `PRODUCTION_STATUS.md` is the component/status table; the paper's new "What is deployed and what is offline" paragraph + per-claim labels point to it. `monitor-pandr/` flagged as a stale 07-21 mirror |
| 2 | Bayesian spatial estimator not checked in; nightly estimator is a centroid method | **Right, and it exposed a paper error**: §2.2 implied the pixel model runs nightly. It never did | §2.2 retitled "(offline analysis)"; the production estimator (pinned centroid + recursive prior) is now described as such, with the MAP fit as its offline validation (~3 px agreement). Stan models + drivers checked in under `spot_paper/code/`; full priors/bounds now printed in the paper |
| 3 | "Honest epochs" not implemented in spotnight; velocity assumes equal 1-night steps | **Right.** The epoch computation lived only in the publisher and even there was reconstructed from filenames | `spotnight.py` now stores `fit_epoch` (mean timestamp of the frames actually fitted) and `prev_epoch` in state; velocity = Δcentre/Δepoch in true px/day; publisher prefers the state epoch. Deployed |
| 4 | Three-layer tracker absent from the snapshot | Deployed on pandr the same morning (template lock + persistence + warp); the reviewer could not have seen it | Synced into `production/pandr/flatfield.py`. Review's robustness asks adopted: **ambiguity gate added** (runner-up correlation peak must trail the winner by ≥0.08); freshness/displacement bounds and atomic persistence were already in |
| 5 | Primary map path lacks the local-sky cap the paper claims | **Right for the snapshot**; the cap shipped same-day in the amplitude-self-cal revision | In `production/pandr/flatfield.py`: `corrected = min(lin·gain, max(B,lin))` on the primary path; synthetic end-to-end still nulls at 0.000% |
| 6 | scurve + self-calibration not combined | Same as 5 — deployed same-day, synced | Combined in the primary path, gated on ring cleanliness, clamp [0.75, 1.35]× |
| 7 | Artifact interfaces don't match; no versioned bundle | **Right.** Minimal contract shipped: `spot_model.json` gains `schema: 2`, `code_version`, `tmap_sha256_16`; `flatfield` verifies hash + finiteness + shape on load and rejects torn publishes | Full manifest (per-array hashes, training-day lists, validation metrics) acknowledged as future work in `PRODUCTION_STATUS.md` |
| 8 | `spotmap.py` clamped-crop coordinate bug (CX=CY=224 vs spot at y≈187/132) | **Right — a real bug in running code**, worse now that the tracked centre is far from the seed | Fixed: local crop coordinates derived from the tracked model with the same clamp rule spotlab uses; regression test noted. (This path feeds research maps only, not the live correction) |
| 9 | Staleness/failure handling largely narrative | Partially right. flatfield age tiers + integrity rejection are deployed; `nightly.sh` relabelled as legacy/research; escalation still lacks an alert transport | Alert transport remains open (listed in PRODUCTION_STATUS "known gaps") |
| 10 | `--shadow` documented but absent; stale comments; no tests | Right on all counts | `--shadow` flag added (explicit no-op, default behaviour); nightly.sh header rewritten; `tests/` created with the synthetic end-to-end test and dipole metric; fuller matrix acknowledged as open |

## Statistical findings

- **Table-2 filters undisclosed** → disclosed verbatim in the caption (level ≥140, dip
  ∈[1%,9%], row-2 ring-structure <3), with the explicit statement that the dip window
  selects on a measured quantity and is not a generic daylight population.
- **Negative-residual accounting** → in the caption: 66/495 negative, 6 < −0.5%,
  1 < −1% (min −1.28%); "inverse bright spot" defined as < −1%.
- **Day-level uncertainty** → computed and printed: 8 days, 11–111 frames/day, per-day
  median removal 83.2–94.3%, day-block bootstrap: removal 88.1% CI [85.6, 90.9],
  residual 0.52% CI [0.40, 0.66]. The result survives blocking.
- **Live 96–99% claim not reproducible** → **withdrawn as a headline.** Replaced by
  `spot_paper/evidence/live_paired.csv` (90 paired ticks with positions, levels,
  structure, model version) and the honest stratification: 07-27 clear evening n=20,
  removal 92.9%, residual +0.35% [−0.80, +1.44]; 07-28 post-deployment (heavy cloud,
  mid-day revisions) n=13, removal 69.7%, residual +1.70%. Clear-day out-of-sample
  evaluation marked pending.
- **Acceptance not guaranteed out-of-sample** → spotnight now validates on run-day
  ticks first and records the borrowed fraction (`acc.borrowed`) when it must top up
  from map-building days.
- **Additive component overclaimed** → paragraph retitled "A response-curve-confounded
  additive residual"; caveats strengthened (autocorrelation, aperture drift — the
  aperture tracks the published centre as of 07-28, so future M1 rows measure the
  feature); "Rayleigh-like" framing dropped from the conclusions.
- **Spatial model under-specified; MAP called posterior** → full Q definition, priors,
  bounds, and indexing added; "MAP state, not a posterior" stated.

## Physical interpretation

The review read a pre-final draft; the abstract/§9 inconsistency it flagged had already
been superseded by the f/4.0 identification (the reviewer's core criticism — that the
"not on the window" inference rested on an unmeasured entrance pupil — was exactly the
error; the published spec resolved it, in the direction opposite the draft's claim).
The v5 abstract, §5 and §9 are consistent: a ~0.25 mm on-glass speck, all apparent
motion attributable to camera/sash flexure through the ~92 px/mm shadow lever, fiducial
test still listed as the definitive discriminator.

## Presentation

Figure 3 clipping fixed (`\resizebox`); table overfull fixed; PDF metadata
(title/author/subject/keywords) set; "Code, data and reproducibility" section added;
abstract rewritten to lead with the artifact, the deployed/offline distinction, the
disclosed population, and the principal limitation. Republished at the same URL.

## What the review changed beyond the checklist

The review's deepest point is architectural: a system whose paper cannot be audited
from its own directory is not done, whatever is running. `production/` +
`PRODUCTION_STATUS.md` + `evidence/` exist so that the next review can be wrong for
more interesting reasons.
