// spotell.stan -- window-spot fit with a TWO-AXIS (elliptical) Gaussian.
//
// Shape is a full 2x2 covariance via (sx, sy, rho), shared across every image in the
// window -- the blemish is one fixed physical object, so only its projected position
// moves. Depth is free per (day x bracket) because the dip's apparent depth is
// tone-curve dependent (dark/short brackets dip deeper). Position is per day, tied
// across days by a drift-informed non-centred random walk.
//
// Constraints kept from the circular version, all learned by having them bite:
//   * sx, sy bounded well below the window half-width, or a broad Gaussian becomes
//     indistinguishable from the background quadratic and MAP drives A -> 0.
//   * A has a positive floor: the blemish is known to exist, and A = 0 is a flat
//     direction in which the position is meaningless.
//   * background basis arrives pre-standardised per map, or it correlates with the
//     per-map level and wrecks the conditioning.
data {
  int<lower=1> N;
  int<lower=1> M;                       // maps = day x bracket
  int<lower=1> D;
  int<lower=1> K;
  array[M] int<lower=1> start;
  array[M] int<lower=1> len;
  array[M] int<lower=1, upper=D> day_of;
  array[M] int<lower=1, upper=K> bkt_of;
  vector[N] u;
  vector[N] v;
  matrix[N, 5] X;
  vector[N] y;
  real<lower=0> pos_sd;
  real<lower=0> step_sd;
  real<lower=0> noise_scale;
}
parameters {
  real cx1;
  real cy1;
  vector[D - 1] cx_step;
  vector[D - 1] cy_step;
  real<lower=15, upper=60> sx;
  real<lower=15, upper=60> sy;
  real<lower=-0.85, upper=0.85> rho;
  vector<lower=0.002, upper=0.10>[K] A;  // one amplitude per bracket class (referee 2b)
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
  real q = 1 - square(rho);
  cx1 ~ normal(0, pos_sd);
  cy1 ~ normal(0, pos_sd);
  cx_step ~ std_normal();
  cy_step ~ std_normal();
  sx ~ normal(33, 7);
  sy ~ normal(33, 7);
  rho ~ normal(0, 0.35);
  A ~ normal(0.02, 0.012);
  b0 ~ normal(1, 0.03);
  to_vector(b) ~ normal(0, 0.01);
  sigma ~ normal(0, noise_scale);
  nu ~ gamma(2, 0.1);

  for (m in 1:M) {
    int s = start[m];
    int L = len[m];
    int d = day_of[m];
    vector[L] du = segment(u, s, L) - cx[d];
    vector[L] dv = segment(v, s, L) - cy[d];
    vector[L] z = (square(du) / square(sx) - 2 * rho * (du .* dv) / (sx * sy)
                   + square(dv) / square(sy)) / q;
    vector[L] blob = A[bkt_of[m]] * exp(-0.5 * z);
    segment(y, s, L) ~ student_t(nu, b0[m] + block(X, s, 1, L, 5) * b[m]' - blob, sigma[m]);
  }
}
generated quantities {
  // major/minor axes and orientation of the fitted ellipse, in px and degrees
  real tr = square(sx) + square(sy);
  real det = square(sx) * square(sy) * (1 - square(rho));
  real disc = sqrt(fmax(square(tr) / 4 - det, 0));
  real sig_major = sqrt(tr / 2 + disc);
  real sig_minor = sqrt(fmax(tr / 2 - disc, 1e-9));
  real theta_deg = 0.5 * atan2(2 * rho * sx * sy, square(sx) - square(sy)) * 180 / pi();
  real axis_ratio = sig_major / sig_minor;
  real r_half_major = sig_major * 1.17741;
}
