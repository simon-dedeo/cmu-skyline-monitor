#!/opt/homebrew/bin/Rscript
# spothier.R -- one hierarchical fit: spot position + shape tied across all image
# stacks, depth per bracket bucket. Writes spot_pos.json for spotlab.py to recentre on.
suppressWarnings(suppressMessages(library(cmdstanr)))
home <- path.expand("~/monitor")
d  <- read.csv("/tmp/spothier/data.csv")
mt <- read.csv("/tmp/spothier/meta.csv", stringsAsFactors = FALSE)
mp <- read.csv("/tmp/spothier/maps.csv", stringsAsFactors = FALSE)
mod <- cmdstan_model(file.path(home, "spothier.stan"))
dat <- list(N = nrow(d), M = nrow(mt), D = max(mt$day_of), K = max(mt$bkt_of),
            map_of = d$map_of, day_of = mt$day_of[order(mt$m)], bkt_of = mt$bkt_of[order(mt$m)],
            u = d$u, v = d$v, un = d$un, vn = d$vn, y = d$y,
            pos_sd = 30, step_sd = 6, amp_mu = 0.02, amp_sd = 0.015,
            sig_mu = 35, sig_sd = 8)
cat(sprintf("data: N=%d M=%d D=%d K=%d\n", dat$N, dat$M, dat$D, dat$K))
t0 <- Sys.time()
fit <- mod$sample(chains = 4, parallel_chains = 4, iter_warmup = 400, iter_sampling = 600,
                  adapt_delta = 0.95, max_treedepth = 12, refresh = 100,
                  show_messages = FALSE, show_exceptions = FALSE, data = dat)
cat(sprintf("sampled in %.0fs\n", as.numeric(difftime(Sys.time(), t0, units = "secs"))))
s <- as.data.frame(fit$summary(c("cx","cy","sg","r_half","A_pct","nu")))
print(s[, c("variable","median","q5","q95","rhat","ess_bulk")], row.names = FALSE)
dg <- fit$diagnostic_summary(quiet = TRUE)
cat(sprintf("divergences=%d  max_rhat=%.4f\n", sum(dg$num_divergent), max(s$rhat, na.rm=TRUE)))

# absolute per-day centres = drift-predicted prior + fitted deviation
udays <- unique(mt[order(mt$day_of), c("day","day_of")])
pc <- unique(mp[, c("day","pcx","pcy")])
udays <- merge(udays, pc, by = "day")
udays <- udays[order(udays$day_of), ]
g <- function(v, col) s[[col]][s$variable == v]
out <- data.frame(day = udays$day,
                  cx = udays$pcx + sapply(udays$day_of, function(i) g(sprintf("cx[%d]", i), "median")),
                  cy = udays$pcy + sapply(udays$day_of, function(i) g(sprintf("cy[%d]", i), "median")),
                  cx_sd = sapply(udays$day_of, function(i) (g(sprintf("cx[%d]",i),"q95") - g(sprintf("cx[%d]",i),"q5")) / 3.29),
                  cy_sd = sapply(udays$day_of, function(i) (g(sprintf("cy[%d]",i),"q95") - g(sprintf("cy[%d]",i),"q5")) / 3.29))
print(out, row.names = FALSE)
write.csv(out, "/tmp/spothier/centres.csv", row.names = FALSE)
cat("-> /tmp/spothier/centres.csv\n")
