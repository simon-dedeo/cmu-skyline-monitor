// spot_m2.stan — SPOT.md model M2: linear-light L_spot = t * L_bg + s, per channel.
// Data are per-tick linearized radiances (per unit exposure time), leverage-gated.
// Student-t likelihood (robust); per-tick measurement sd from the annulus fit plus a
// fitted extra-scatter term. Priors per SPOT.md §4.
data {
  int<lower=1> N;
  vector[N] Lbg;              // linearized background radiance under the disk (annulus fit)
  vector[N] Lsp;              // linearized spot-disk radiance
  vector<lower=0>[N] sig;     // per-tick measurement sd (linearized)
}
parameters {
  real<lower=0, upper=1> t;   // transmittance
  real s;                     // additive scatter radiance (can straddle 0 under noise)
  real<lower=1> nu;
  real<lower=0> sig_x;        // extra scatter (systematics)
}
model {
  1 - t ~ lognormal(log(0.03), 0.7);
  s ~ normal(0, 0.02);
  nu ~ gamma(2, 0.1);
  sig_x ~ normal(0, 0.01);
  Lsp ~ student_t(nu, t * Lbg + s, sqrt(square(sig) + square(sig_x)));
}
