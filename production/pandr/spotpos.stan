// spotpos.stan -- joint Bayesian fit of window-spot LOCATION, depth and width from a
// per-day median-stacked, level-normalised image window. Single Gaussian blob on a
// smooth quadratic background; Student-t noise so cloud edges don't drag the centre.
// Priors are deliberately tight: the spot is a fixed window blemish whose position is
// known to a few tens of px from the day-0 fit plus measured camera drift.
//
// The polynomial basis is fed in PRE-SCALED to [-1,1] (un, vn) while the blob geometry
// stays in pixels. Mixing the two scales is what made the first version degenerate:
// with raw-pixel u^2 (up to 2.2e4) a loosely-prior'd quadratic could absorb the blob.
data {
  int<lower=1> N;
  vector[N] u;              // px offset from prior centre (east)
  vector[N] v;              // px offset from prior centre (south)
  vector[N] un;             // u / half_window, in [-1,1]
  vector[N] vn;             // v / half_window
  vector[N] y;              // level-normalised intensity, ~1 on clean sky
  real<lower=0> pos_sd;     // prior sd on centre offset, px
  real<lower=0> amp_mu;     // prior mean fractional depth
  real<lower=0> sig_mu;     // prior mean Gaussian sigma, px
  real<lower=0> sig_sd;
}
parameters {
  real cx;
  real cy;
  real<lower=0, upper=0.30> A;
  real<lower=8, upper=90> sg;
  real b0;
  vector[5] b;
  real<lower=0> sigma;
  real<lower=2> nu;
}
model {
  vector[N] blob = A * exp(-0.5 * (square(u - cx) + square(v - cy)) / square(sg));
  vector[N] bg = b0 + b[1]*un + b[2]*vn + b[3]*square(un) + b[4]*(un .* vn) + b[5]*square(vn);

  cx ~ normal(0, pos_sd);
  cy ~ normal(0, pos_sd);
  A  ~ normal(amp_mu, 0.012);
  sg ~ normal(sig_mu, sig_sd);
  b0 ~ normal(1, 0.03);
  b  ~ normal(0, 0.02);          // <= ~4% gradient across the window
  sigma ~ normal(0, 0.01);
  nu ~ gamma(2, 0.1);

  y ~ student_t(nu, bg - blob, sigma);
}
generated quantities {
  real r_half = sg * 1.17741;
}
