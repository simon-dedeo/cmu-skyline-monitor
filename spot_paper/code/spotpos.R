#!/opt/homebrew/bin/Rscript
# spotpos.R -- fit spot LOCATION + depth + width per day via Stan (spotpos.stan).
suppressWarnings(suppressMessages(library(cmdstanr)))
home <- path.expand("~/monitor")
pri  <- read.csv("/tmp/spotpos/priors.csv", stringsAsFactors = FALSE)
days <- commandArgs(trailingOnly = TRUE)
if (length(days)) pri <- pri[pri$day %in% days, ]
mod  <- cmdstan_model(file.path(home, "spotpos.stan"))
res  <- NULL
for (i in seq_len(nrow(pri))) {
  p <- pri[i, ]
  d <- read.csv(sprintf("/tmp/spotpos/%s.csv", p$day))
  t0 <- Sys.time()
  fit <- mod$sample(data = list(N = nrow(d), u = d$u, v = d$v, un = d$un, vn = d$vn,
                                y = d$y, pos_sd = 30, amp_mu = 0.02,
                                sig_mu = 35, sig_sd = 8),
                    chains = 4, parallel_chains = 4, iter_warmup = 400,
                    iter_sampling = 600, adapt_delta = 0.95, refresh = 0,
                    show_messages = FALSE, show_exceptions = FALSE)
  s <- as.data.frame(fit$summary(c("cx","cy","A","sg","r_half","nu","sigma")))
  g <- function(v, col) s[[col]][s$variable == v]
  dg <- fit$diagnostic_summary(quiet = TRUE)
  res <- rbind(res, data.frame(
    day = p$day, n = nrow(d),
    cx = p$pcx + g("cx","median"), cx_lo = p$pcx + g("cx","q5"), cx_hi = p$pcx + g("cx","q95"),
    cy = p$pcy + g("cy","median"), cy_lo = p$pcy + g("cy","q5"), cy_hi = p$pcy + g("cy","q95"),
    A_pct = 100*g("A","median"), A_lo = 100*g("A","q5"), A_hi = 100*g("A","q95"),
    sg = g("sg","median"), r_half = g("r_half","median"), nu = g("nu","median"),
    rhat = max(s$rhat, na.rm = TRUE), div = sum(dg$num_divergent),
    secs = round(as.numeric(difftime(Sys.time(), t0, units="secs")), 1)))
  r <- tail(res, 1)
  cat(sprintf("%s  centre=(%.1f,%.1f) x[%.0f,%.0f] y[%.0f,%.0f]  A=%.2f%%[%.2f,%.2f]  sg=%.1f  r_half=%.0f  nu=%.1f  rhat=%.4f div=%d  %.0fs\n",
      r$day, r$cx, r$cy, r$cx_lo, r$cx_hi, r$cy_lo, r$cy_hi, r$A_pct, r$A_lo, r$A_hi,
      r$sg, r$r_half, r$nu, r$rhat, r$div, r$secs))
}
write.csv(res, "/tmp/spotpos/fits.csv", row.names = FALSE)
cat("-> /tmp/spotpos/fits.csv\n")
