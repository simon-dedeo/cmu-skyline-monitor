// spot_m2.stan -- 3-channel Bayesian Student-t model of window-blemish transmittance
// in empirical-CRF linear radiance (akdeniz nightly fit -> live flatfield prior).
//   y[n] = L_spot / L_bg ~ Student-t(nu[c], t[c] + k_fac[c] * (L_fac / L_bg), hypot(sig[n], sig_x[c]))
// where t[c] is pure optical transmittance and k_fac[c] captures forward-scattered sunlight
// off the glass grain (proxied by sunlit courtyard facade radiance L_fac).
data {
  int<lower=1> N;
  array[N] int<lower=1, upper=3> ch;   // 1=B, 2=G, 3=R
  vector[N] y;                         // observed L_spot / L_bg
  vector[N] fac_ratio;                 // L_facade / L_bg
  vector<lower=0>[N] sig;              // per-tick measurement sd
}
parameters {
  vector<lower=0.80, upper=1.0>[3] t;
  vector[3] k_fac;
  vector<lower=2, upper=50>[3] nu;
  vector<lower=1e-4, upper=0.1>[3] sig_x;
}
model {
  1.0 - t ~ lognormal(log(0.05), 0.5);
  k_fac ~ normal(0, 0.02);
  nu ~ gamma(2, 0.15);
  sig_x ~ normal(0, 0.015);
  vector[N] mu = t[ch] + k_fac[ch] .* fac_ratio;
  vector[N] sd = sqrt(square(sig) + square(sig_x[ch]));
  y ~ student_t(nu[ch], mu, sd);
}
