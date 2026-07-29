/-
Machine-checked verification of the quantitative spine of Section 10
("Validating and extending the literature") of

    *Pittsburgh in a Grain of Sand* (v11, 2026-07-28)
    github.com/simon-dedeo/cmu-skyline-monitor

Scope, honestly stated: Lean cannot check wave optics, statistics, or the
measurements themselves. What it CAN check — and what this file checks — is that
the paper's arithmetic and model bookkeeping are exactly right: every constant
below enters as a hypothesis (a measured or specified number from the paper), and
every theorem is closed by exact rational arithmetic. No axioms beyond Mathlib,
no `sorry`.

Numbers used (paper Sections 1, 9, 10):
  speck diameter        s  = 0.25 mm
  entrance pupil        a  = 1.1 mm
  pupil-to-pane         d  ∈ [30, 35] mm
  wavelengths           λ_B, λ_G, λ_R = 460, 540, 610 nm
  measured core depths  4.5–5.4 %
  measured integrals    I_B : I_G : I_R = 1.00 : 1.08 : 1.07
  sensor                3840 × 2160, 90° diagonal field of view
-/
import Mathlib

namespace SpotFit

/-! ## 1. Extinction bookkeeping: the instrument–particle pair

A particle large compared to the wavelength removes twice its geometric blocking
from a beam, half of it deposited in a narrow forward-diffraction lobe (the
"extinction paradox"). A detector that recollects a fraction `f` of that forward
lobe registers an effective extinction efficiency `Qmeas f = 2 - f`. Section 10's
claim — *measured extinction is a property of the instrument–particle pair, not of
the particle* — is, at the bookkeeping level, the statement that `Qmeas` is a
non-constant (indeed strictly antitone) function of the detector parameter. -/

/-- Effective measured extinction efficiency, in units of the geometric cross
section, when the detector recollects a fraction `f ∈ [0,1]` of the
forward-diffracted half of the removed power. -/
def Qmeas (f : ℚ) : ℚ := 2 - f

/-- Far field, narrow acceptance (`f = 0`): the full extinction paradox value. -/
theorem Qmeas_far_field : Qmeas 0 = 2 := by norm_num [Qmeas]

/-- Wide acceptance (`f = 1`, our regime): geometric blocking only. -/
theorem Qmeas_wide_acceptance : Qmeas 1 = 1 := by norm_num [Qmeas]

/-- More recollection strictly means less registered extinction. -/
theorem Qmeas_strictAnti : StrictAnti Qmeas := by
  intro a b hab
  simp only [Qmeas]
  linarith

/-- The same particle yields different measured extinction for different
instruments: the measured quantity is not a particle property. -/
theorem Qmeas_instrument_dependent : Qmeas 0 ≠ Qmeas 1 := by
  norm_num [Qmeas]

/-- The measured value always lies between geometric blocking and the paradox
value — the 1×-to-2× continuum of Lai, Wong & Wong (2004). -/
theorem Qmeas_between {f : ℚ} (h0 : 0 ≤ f) (h1 : f ≤ 1) :
    1 ≤ Qmeas f ∧ Qmeas f ≤ 2 := by
  constructor <;> (simp only [Qmeas]; linarith)

/-! ## 2. The angle hierarchy

Section 10: the pupil half-angle (≈18 mrad) stands 7–10× *wider* than the speck's
whole-body forward-diffraction lobe (θ ≈ λ/s ≈ 2 mrad) — so the forward lobe is
recollected, `f ≈ 1` — yet ~57× *smaller* than the ≈60° (≈1.047 rad) aperture a
circular on-axis detector needs before a transmission measurement converges to the
true extinction cross section (Markel 2020, as an order-of-magnitude guide).

All lengths in millimetres; angles in radians as exact rationals. -/

/-- Forward-diffraction lobe half-width `λ/s` (radians) for wavelength `lam` (mm)
and speck diameter 0.25 mm. -/
def lobe (lam : ℚ) : ℚ := lam / (25/100)

/-- Pupil acceptance half-angle `a/(2d)` (radians) for pupil 1.1 mm at
pane distance `d` (mm). -/
def acc (d : ℚ) : ℚ := (11/10) / (2 * d)

/-- At the central estimate (λ = 530 nm, d = 32 mm) the pupil acceptance is
between 7 and 10 lobe half-widths: the "7–10 times wider" of Section 10. -/
theorem pupil_7_to_10_lobes :
    7 < acc 32 / lobe (53/100000) ∧ acc 32 / lobe (53/100000) < 10 := by
  norm_num [acc, lobe]

/-- Across the full parameter window (λ ∈ [460, 610] nm, d ∈ [30, 35] mm) the
pupil acceptance exceeds 6 lobe half-widths — the forward lobe is always well
inside the collection cone. -/
theorem pupil_wider_than_lobe (lam d : ℚ)
    (hl : 46/100000 ≤ lam) (hl' : lam ≤ 61/100000)
    (hd : 30 ≤ d) (hd' : d ≤ 35) :
    6 * lobe lam < acc d := by
  have hd0 : (0 : ℚ) < 2 * d := by linarith
  simp only [lobe, acc]
  rw [← mul_div_assoc, div_lt_div_iff₀ (by norm_num) hd0]
  nlinarith [mul_le_mul hl' hd' (by linarith : (0:ℚ) ≤ d)
    (by norm_num : (0:ℚ) ≤ 61/100000)]

/-- Yet the acceptance is more than 57× below the ≈60° (1047/1000 rad)
convergence criterion for measuring true extinction: the instrument sits deep in
the wide-acceptance (absorption-like) regime, not the extinction-measuring one. -/
theorem acceptance_57x_below_convergence (d : ℚ) (hd : 30 ≤ d) (_hd' : d ≤ 35) :
    57 * acc d < 1047/1000 := by
  have hd0 : (0 : ℚ) < 2 * d := by linarith
  simp only [acc]
  rw [← mul_div_assoc, div_lt_iff₀ hd0]
  linarith

/-! ## 3. Geometric blocking, not the paradoxical factor of two

The inspected ~0.25 mm speck against the 1.1 mm pupil predicts a projected-area
obstruction `(s/a)² = 625/12100 ≈ 5.17 %`. The measured per-channel core depths
run 4.5–5.4 %. Section 10 (as revised for the second referee): the measurement
*instantiates* the wide-acceptance regime — the geometric value lies inside the
measured window and the doubled value far outside it — without adjudicating the
far-field cross section. -/

/-- Projected-area obstruction fraction `(s/a)²`. -/
def obstruction : ℚ := (25/110) ^ 2

/-- Shadow depth under recollection fraction `f`: `Qmeas f × (s/a)²`. -/
def depth (f : ℚ) : ℚ := Qmeas f * obstruction

/-- The geometric prediction (full recollection, `f = 1`) lies inside the
measured window [4.5 %, 5.4 %]. -/
theorem geometric_depth_in_measured_window :
    45/1000 ≤ depth 1 ∧ depth 1 ≤ 54/1000 := by
  norm_num [depth, Qmeas, obstruction]

/-- The far-field paradox value (`f = 0`, i.e. 2×(s/a)² ≈ 10.3 %) lies outside
the measured window. -/
theorem paradox_depth_outside_measured_window :
    ¬ (45/1000 ≤ depth 0 ∧ depth 0 ≤ 54/1000) := by
  norm_num [depth, Qmeas, obstruction]

/-! ## 4. The λ⁻² recollection law and the green-channel prediction

Micron-scale surface texture scatters into lobes of width ∝ λ; the fraction
recollected by a fixed acceptance cone scales as 1/λ². Model the recollected
fraction as `ρ(λ) = K/λ²`, calibrate `K` on the measured blue/red integrated
deficits (1.00 : 1.07), and the green channel is *predicted* with no further
freedom. Section 10: prediction 1.045, measurement 1.08 — within 3.3 %.

Wavelengths in units of 10 nm: B = 46, G = 54, R = 61. Integrals are relative to
blue = 1; a channel's integral is proportional to `1 − ρ(λ)`. -/

/-- The calibration constant solved exactly from `(1−ρ_B)/(1−ρ_R) = 100/107`. -/
def K : ℚ := 55115452 / 186547

/-- Recollected fraction at wavelength `lam` (units of 10 nm). -/
def ρ (lam : ℚ) : ℚ := K / lam ^ 2

/-- The calibration reproduces the measured blue:red ratio exactly. -/
theorem calibration_exact : (1 - ρ 46) / (1 - ρ 61) = 100 / 107 := by
  norm_num [ρ, K]

/-- The solved red recollected fraction is ≈ 8 % of the blocked light
(Section 10 quotes ρ_R ≈ 8 %). -/
theorem rhoR_approx_8_percent : 79/1000 < ρ 61 ∧ ρ 61 < 80/1000 := by
  norm_num [ρ, K]

/-- The solved blue recollected fraction is ≈ 14 % (Section 10: ρ_B ≈ 14 %). -/
theorem rhoB_approx_14_percent : 139/1000 < ρ 46 ∧ ρ 46 < 140/1000 := by
  norm_num [ρ, K]

/-- Shorter wavelengths are recollected more: ρ_B > ρ_G > ρ_R. Equivalently the
predicted core refilling orders the depths red > green > blue — the measured
ordering (5.38 % > 4.89 % > 4.49 %). -/
theorem recollection_ordering : ρ 61 < ρ 54 ∧ ρ 54 < ρ 46 := by
  norm_num [ρ, K]

/-- The green integral predicted from blue and red alone, on the scale where
red = 107/100. -/
def greenPredicted : ℚ := (107/100) * (1 - ρ 54) / (1 - ρ 61)

/-- The prediction evaluates to ≈ 1.0445 (the paper's 1.045). -/
theorem green_prediction_value :
    1044/1000 < greenPredicted ∧ greenPredicted < 1045/1000 := by
  norm_num [greenPredicted, ρ, K]

/-- The prediction lands within 3.4 % of the measured green integral 1.08 —
the paper's "within 3.3 %" (0.0355/1.08 = 3.29 %), checked here as a strict
bound: |predicted − 1.08| < 0.034 × 1.08. -/
theorem green_prediction_within_scatter :
    108/100 - greenPredicted < (34/1000) * (108/100) ∧
    greenPredicted < 108/100 := by
  norm_num [greenPredicted, ρ, K]

/-! ## 5. The lever correction (second referee, finding 7 — machine-checked)

For a 3840 × 2160 sensor with a 90° diagonal field of view, the focal length in
pixels is `f_px = √(3840² + 2160²)/2` (tan 45° = 1). The near-field translational
lever is `f_px/d`. The referee observed that the paper's original 92 px/mm is
impossible on axis; v11 adopts 63–73 px/mm. Both directions are verified here
from the integer bounds `4405² < 3840² + 2160² < 4406²`. -/

/-- The sensor diagonal in pixels lies strictly between 4405 and 4406. -/
theorem diag_bounds : (4405 : ℕ) ^ 2 < 3840 ^ 2 + 2160 ^ 2 ∧
    3840 ^ 2 + 2160 ^ 2 < (4406 : ℕ) ^ 2 := by norm_num

/-- On-axis lever bounds: for any `f_px` with `f_px² = (3840² + 2160²)/4` and any
pane distance `d ∈ [30, 35]` mm, the lever `f_px/d` lies in (62.9, 73.5) px/mm —
the paper's corrected 63–73 px/mm. -/
theorem lever_window (fpx d : ℚ) (hf : fpx ^ 2 = 19411200 / 4) (hpos : 0 < fpx)
    (hd : 30 ≤ d) (hd' : d ≤ 35) :
    629/10 < fpx / d ∧ fpx / d < 735/10 := by
  have hd0 : (0 : ℚ) < d := by linarith
  have hub : fpx < 2203 := by nlinarith [sq_nonneg (fpx - 2203)]
  have hlb : (22025 : ℚ)/10 < fpx := by
    nlinarith [mul_pos (sub_pos.mpr hub) hpos]
  constructor
  · rw [lt_div_iff₀ hd0]; nlinarith
  · rw [div_lt_iff₀ hd0]; nlinarith

/-- The original 92 px/mm claim is refuted: it exceeds the on-axis lever at even
the nearest admissible pane distance. -/
theorem old_92_claim_refuted (fpx : ℚ) (hf : fpx ^ 2 = 19411200 / 4)
    (_hpos : 0 < fpx) : fpx / 30 < 92 := by
  have hub : fpx < 2203 := by nlinarith [sq_nonneg (fpx - 2203)]
  rw [div_lt_iff₀ (by norm_num : (0:ℚ) < 30)]
  linarith

/-- Consequently the 120 px week-long walk corresponds to 1.63–1.91 mm of
camera–pane flexure on axis (the paper's corrected 1.4–1.9 mm range additionally
admits the off-axis plate-scale factor, not modelled here). -/
theorem flexure_window (fpx d : ℚ) (hf : fpx ^ 2 = 19411200 / 4) (hpos : 0 < fpx)
    (hd : 30 ≤ d) (hd' : d ≤ 35) :
    163/100 < 120 / (fpx / d) ∧ 120 / (fpx / d) < 191/100 := by
  obtain ⟨h1, h2⟩ := lever_window fpx d hf hpos hd hd'
  have hld : 0 < fpx / d := by positivity
  constructor
  · rw [lt_div_iff₀ hld]; linarith
  · rw [div_lt_iff₀ hld]; linarith

end SpotFit
