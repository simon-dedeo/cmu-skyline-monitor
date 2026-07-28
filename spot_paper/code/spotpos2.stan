// spotpos2.stan -- POSITION-focused fit. One spot centre per day, tied across days by a
// drift-informed random walk; ONE shared width; one depth per day. Deliberately tight
// priors, because we are asking "where is it", not "does it exist".
//
// Two hard-won constraints, both about identifiability rather than statistics:
//   * sg is bounded well below the window half-width. A Gaussian with sg ~ 90 px inside a
//     +/-110 px window is indistinguishable from the background quadratic, and MAP duly
//     drove A -> 0.000% and called it the best fit.
//   * A has a positive lower bound. The spot is a known, visible blemish (3-7% when
//     prominent); allowing A = 0 hands the optimiser a flat direction in which the
//     position means nothing.
data {
  int<lower=1> N;
  int<lower=1> M;                       // maps == days here
  array[M] int<lower=1> start;
  array[M] int<lower=1> len;
  vector[N] u;                          // px from that day's drift-predicted centre
  vector[N] v;
  matrix[N, 5] X;                       // standardised background basis
  vector[N] y;
  real<lower=0> pos_sd;
  real<lower=0> step_sd;
  real<lower=0> noise_scale;
}
parameters {
  real cx1;
  real cy1;
  vector[M - 1] cx_step;
  vector[M - 1] cy_step;
  real<lower=22, upper=52> sg;
  vector<lower=0.002, upper=0.10>[M] A;
  vector[M] b0;
  matrix[M, 5] b;
  vector<lower=0>[M] sigma;
  real<lower=2> nu;
}
transformed parameters {
  vector[M] cx;
  vector[M] cy;
  cx[1] = cx1;
  cy[1] = cy1;
  for (m in 2:M) {
    cx[m] = cx[m - 1] + step_sd * cx_step[m - 1];
    cy[m] = cy[m - 1] + step_sd * cy_step[m - 1];
  }
}
model {
  cx1 ~ normal(0, pos_sd);
  cy1 ~ normal(0, pos_sd);
  cx_step ~ std_normal();
  cy_step ~ std_normal();
  sg ~ normal(38, 5);
  A  ~ normal(0.02, 0.01);
  b0 ~ normal(1, 0.03);
  to_vector(b) ~ normal(0, 0.01);
  sigma ~ normal(0, noise_scale);
  nu ~ gamma(2, 0.1);

  for (m in 1:M) {
    int s = start[m];
    int L = len[m];
    vector[L] uu = segment(u, s, L);
    vector[L] vv = segment(v, s, L);
    vector[L] blob = A[m] * exp(-0.5 * (square(uu - cx[m]) + square(vv - cy[m])) / square(sg));
    segment(y, s, L) ~ student_t(nu, b0[m] + block(X, s, 1, L, 5) * b[m]' - blob, sigma[m]);
  }
}
generated quantities {
  real r_half = sg * 1.17741;
  vector[M] A_pct = 100 * A;
}
