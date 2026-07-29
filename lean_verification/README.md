# Lean verification of Section 10

A machine-checked verification (Lean 4 + Mathlib, toolchain `v4.33.0-rc1`) of the
quantitative spine of Section 10 of *Pittsburgh in a Grain of Sand*.

## What is verified

`SpotFit/Section10.lean`, five blocks mirroring the section:

1. **Extinction bookkeeping** — `Qmeas f = 2 − f` for recollected fraction `f`:
   endpoints (`Qmeas 0 = 2` far-field paradox, `Qmeas 1 = 1` geometric blocking),
   strict antitonicity, instrument dependence, and the 1×–2× continuum
   (`Qmeas_between`) of Lai, Wong & Wong.
2. **Angle hierarchy** — the pupil acceptance is 7–10× the diffraction-lobe
   half-width at the central estimate, `> 6×` across the whole parameter window
   (λ ∈ [460, 610] nm, d ∈ [30, 35] mm), yet `> 57×` below the ≈60° convergence
   criterion. Forward lobe recollected; extinction regime absent.
3. **Geometric blocking** — `(s/a)² = 625/12100` lies inside the measured depth
   window [4.5 %, 5.4 %]; the doubled (paradox) value lies outside it.
4. **λ⁻² recollection** — the calibration constant solved *exactly* from the
   blue:red integrals (100 : 107) gives ρ_R ≈ 8 %, ρ_B ≈ 14 %, the R > G > B depth
   ordering, and a green-channel prediction of 1.0445 — within 3.4 % of the
   measured 1.08, as a strict rational bound.
5. **The lever correction** (second referee, finding 7, machine-checked) — from
   `4405² < 3840² + 2160² < 4406²`: the on-axis lever lies in (62.9, 73.5) px/mm
   for d ∈ [30, 35] mm, the paper's original **92 px/mm is refuted**
   (`old_92_claim_refuted`), and the 120 px walk corresponds to 1.63–1.91 mm of
   on-axis flexure.

All constants enter as hypotheses (measured or specified numbers from the paper);
every proof closes by exact rational arithmetic (`norm_num` / `linarith` /
`nlinarith`). No `sorry`; `#print axioms` reports only `propext`,
`Classical.choice`, `Quot.sound` for every theorem.

## What is deliberately NOT verified

Lean checks arithmetic and model bookkeeping, not physics or statistics: the wave
optics behind `Qmeas`, the λ⁻² lobe model, the measurements themselves, and the
Bayesian estimation pipeline are all *inputs* here, not theorems. This file makes
the paper's Section 10 reasoning inspectable and its numbers incorruptible — it
does not make them true.

## Building

```
cd lean_verification
lake build          # requires elan; Mathlib fetched per lake-manifest.json
```
(`.lake/` is gitignored; first build on a fresh machine wants
`lake exe cache get` to avoid compiling Mathlib.)
