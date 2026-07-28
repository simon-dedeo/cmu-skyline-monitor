#!/opt/homebrew/bin/Rscript
# spotfit.R — nightly Bayesian fit of the window-spot model (SPOT.md §4), fully local on
# the Air. Fits M2 (L_spot = t*L_bg + s per channel, Student-t) in Stan via cmdstanr on the
# leverage-gated photometry, plus the M1 falsification regression (dip_lin ~ 1/L_bg: a
# nonzero slope demands the additive term). Falls back to robust rlm + bootstrap if
# cmdstan is unavailable, so a toolchain hiccup never stops the nightly record.
# Outputs: spot_lab/fits/<date>/{summary.csv, m1_falsification.csv, diag.txt}
suppressWarnings(suppressMessages({
  ok_cmdstan <- requireNamespace("cmdstanr", quietly = TRUE) &&
                !inherits(try(cmdstanr::cmdstan_version(), silent = TRUE), "try-error")
  library(MASS)
}))
home  <- path.expand("~/monitor")
csvp  <- file.path(home, "spot_lab", "spot_photometry.csv")
outd  <- file.path(home, "spot_lab", "fits", format(Sys.Date(), "%Y%m%d"))
dir.create(outd, recursive = TRUE, showWarnings = FALSE)
diagf <- file.path(outd, "diag.txt")
say   <- function(...) cat(sprintf(...), "\n", file = diagf, append = TRUE)
say("spotfit %s  cmdstan=%s", format(Sys.time(), tz = "UTC"), ok_cmdstan)

d <- tryCatch(read.csv(csvp, stringsAsFactors = FALSE), error = function(e) NULL)
if (is.null(d) || nrow(d) < 30) { say("insufficient data (%s rows)", ifelse(is.null(d), 0, nrow(d))); quit(status = 0) }

srgb_lin <- function(v) { v <- v / 255; ifelse(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ^ 2.4) }

d <- d[d$patch == "spot" & d$gain == 0 & grepl("^(br|ref|ladder_exp)", d$role), ]
d <- d[is.finite(d$bg_fit) & is.finite(d$disk_med) & d$exp_units > 0, ]
d <- d[d$clip_hi < 0.005 & d$clip_lo < 0.005, ]                       # unclipped
d <- d[d$ann_resid_mad < stats::quantile(d$ann_resid_mad, 0.9, na.rm = TRUE), ]  # annulus-uniformity gate
say("gated rows: %d over %d distinct ticks", nrow(d), length(unique(d$ts_utc)))
if (nrow(d) < 50) { say("insufficient gated rows"); quit(status = 0) }

tau     <- d$exp_units * 1e-4                                          # seconds
d$Lbg   <- srgb_lin(d$bg_fit)   / tau
d$Lsp   <- srgb_lin(d$disk_med) / tau
d$sig   <- pmax((srgb_lin(pmin(d$bg_fit + d$ann_resid_mad, 255)) -
                 srgb_lin(pmax(d$bg_fit - d$ann_resid_mad, 0))) / 2 / tau, 1e-6)

summ <- NULL; fals <- NULL
for (ch in c("b", "g", "r")) {
  x <- d[d$ch == ch, ]
  if (nrow(x) < 30) next
  # M1 falsification: dip_lin ~ 1/L_bg (slope != 0 => additive term needed)
  fl <- tryCatch(summary(lm(dip_lin ~ I(1 / Lbg), data = x))$coefficients, error = function(e) NULL)
  if (!is.null(fl) && nrow(fl) == 2)
    fals <- rbind(fals, data.frame(ch = ch, slope = fl[2, 1], se = fl[2, 2], p = fl[2, 4], n = nrow(x)))
  if (ok_cmdstan) {
    fit <- tryCatch({
      mod <- cmdstanr::cmdstan_model(file.path(home, "spot_m2.stan"))
      mod$sample(data = list(N = nrow(x), Lbg = x$Lbg, Lsp = x$Lsp, sig = x$sig),
                 chains = 4, parallel_chains = 4, iter_warmup = 500, iter_sampling = 1000,
                 refresh = 0, show_messages = FALSE)
    }, error = function(e) { say("stan[%s] failed: %s", ch, conditionMessage(e)); NULL })
    if (!is.null(fit)) {
      s <- as.data.frame(fit$summary(c("t", "s", "nu", "sig_x")))
      summ <- rbind(summ, data.frame(
        ch = ch, method = "stan", n = nrow(x),
        t_med = s$median[1], t_q05 = s$q5[1], t_q95 = s$q95[1],
        s_med = s$median[2], s_q05 = s$q5[2], s_q95 = s$q95[2],
        nu = s$median[3], sig_x = s$median[4], rhat_max = max(s$rhat, na.rm = TRUE)))
      next
    }
  }
  # fallback: robust regression + bootstrap CIs (never leave a night without an estimate)
  co <- tryCatch({
    boot <- replicate(200, { i <- sample(nrow(x), replace = TRUE)
                             coef(MASS::rlm(Lsp ~ Lbg, data = x[i, ], maxit = 60)) })
    c(coef(MASS::rlm(Lsp ~ Lbg, data = x, maxit = 60)),
      quantile(boot[2, ], c(.05, .95)), quantile(boot[1, ], c(.05, .95)))
  }, error = function(e) NULL)
  if (!is.null(co))
    summ <- rbind(summ, data.frame(ch = ch, method = "rlm_boot", n = nrow(x),
      t_med = co[2], t_q05 = co[3], t_q95 = co[4],
      s_med = co[1], s_q05 = co[5], s_q95 = co[6], nu = NA, sig_x = NA, rhat_max = NA))
}
if (!is.null(summ)) { write.csv(summ, file.path(outd, "summary.csv"), row.names = FALSE); say("summary written (%d channels)", nrow(summ)) }
if (!is.null(fals)) write.csv(fals, file.path(outd, "m1_falsification.csv"), row.names = FALSE)
say("done")
