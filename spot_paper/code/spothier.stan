// LEGACY (referee 2b): exploratory only; superseded by spotell.stan + run_map.sh.
// Not the model printed in the paper. Retained for the record.
// spothier.stan -- hierarchical window-spot model: ONE fit over all (day x bracket)
// image stacks, with the spot's POSITION and SHAPE tied across every image and its
// DEPTH free to vary with exposure.
//
// Why joint rather than per-image: on a single stack the ~1% dip trades off against that
// image's background quadratic, so the amplitude drifts to 0 and the position collapses
// to its prior (measured: A = 0.03% [0, 2.38], rhat 2.15). Pooling breaks the degeneracy
// -- one shared centre and width are constrained by every image at once, while the known
// exposure-dependence of the depth (short/dark brackets dip deeper) gives the blob a
// signature a smooth background cannot mimic.
//
// The position prior comes from the drift data: each map's pixel coordinates are already
// expressed relative to that day's drift-predicted centre, so the day centres are small
// DEVIATIONS from that prediction, following a tight random walk (the mount settles
// smoothly, it does not teleport). The walk is non-centred for geometry.
//
// Conditioning notes, both learned the hard way:
//   * pixels are laid out in contiguous per-map blocks so the likelihood vectorises;
//     a scalar loop over all N ran ~50x slower.
//   * the 5 background basis columns arrive PRE-STANDARDISED per map (zero mean, unit
//     sd). Raw un^2/vn^2 have large nonzero means, so they correlate strongly with the
//     per-map level b0 and NUTS saturated max_treedepth on the resulting ridge.
data {
  int<lower=1> N;
  int<lower=1> M;
  int<lower=1> D;
  int<lower=1> K;
  array[M] int<lower=1> start;
  array[M] int<lower=1> len;
  array[M] int<lower=1, upper=D> day_of;
  array[M] int<lower=1, upper=K> bkt_of;
  vector[N] u;                          // px offset from the day's drift-predicted centre
  vector[N] v;
  matrix[N, 5] X;                       // standardised background basis
  vector[N] y;
  real<lower=0> pos_sd;
  real<lower=0> step_sd;
  real<lower=0> amp_mu;
  real<lower=0> amp_sd;
  real<lower=0> sig_mu;
  real<lower=0> sig_sd;
  real<lower=0> noise_scale;            // rough residual scale, sets the sigma prior
}
parameters {
  real cx1;
  real cy1;
  vector[D - 1] cx_step;                // non-centred walk increments
  vector[D - 1] cy_step;
  real<lower=8, upper=90> sg;
  vector<lower=0, upper=0.30>[K] A;
  vector[M] b0;
  matrix[M, 5] b;
  vector<lower=0>[M] sigma;
  real<lower=2> nu;
}
transformed parameters {
  vector[D] cx;
  vector[D] cy;
  cx[1] = cx1;
  cy[1] = cy1;
  for (d in 2:D) {
    cx[d] = cx[d - 1] + step_sd * cx_step[d - 1];
    cy[d] = cy[d - 1] + step_sd * cy_step[d - 1];
  }
}
model {
  cx1 ~ normal(0, pos_sd);
  cy1 ~ normal(0, pos_sd);
  cx_step ~ std_normal();
  cy_step ~ std_normal();
  sg ~ normal(sig_mu, sig_sd);
  A  ~ normal(amp_mu, amp_sd);
  b0 ~ normal(1, 0.03);
  to_vector(b) ~ normal(0, 0.01);
  sigma ~ normal(0, noise_scale);
  nu ~ gamma(2, 0.1);

  for (m in 1:M) {
    int s = start[m];
    int L = len[m];
    int d = day_of[m];
    vector[L] uu = segment(u, s, L);
    vector[L] vv = segment(v, s, L);
    vector[L] blob = A[bkt_of[m]]
                     * exp(-0.5 * (square(uu - cx[d]) + square(vv - cy[d])) / square(sg));
    vector[L] mu = b0[m] + block(X, s, 1, L, 5) * b[m]' - blob;
    segment(y, s, L) ~ student_t(nu, mu, sigma[m]);
  }
}
generated quantities {
  real r_half = sg * 1.17741;
  vector[K] A_pct = 100 * A;
}
