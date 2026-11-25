# Production status — window-spot correction system

Snapshot date: **2026-11-24** (`2026-11-24-bayes3`, schema 3).
This table is the implementation-status ground truth for `spot_paper/SPOT_FIT.pdf` (v12);
`production/` contains byte-copies of the deployed files as of this date.

| component | file (deployed host) | snapshot copy | status |
|---|---|---|---|
| Live Bayesian MAP corrector: empirical CRF linearization, Stan M2 physical prior (`t`, `k_fac * L_fac/L_bg`), pooled matched-filter conjugate shrinkage, clipped-bracket inheritance, `np.rint()` rounding, `TOP_MASK=20` + ridge sky fit, symmetric top-edge lock, HDR-only provenance | `pandr:~/monitor/flatfield.py` | `production/pandr/flatfield.py` | **deployed 2026-11-24** (`2026-11-24-bayes3`) |
| Fusion + enhance (correction applied pre-fusion) | `pandr:~/monitor/hdrfuse.py` | `production/pandr/hdrfuse.py` | deployed |
| Unified nightly pipeline: parallelized pinned-centroid tracker (adaptive 2–4 d, 10-night velocity), empirical CRF fit, vectorized 3-channel Stan M2 NUTS fit (`cmdstanpy`), multi-bracket registered CRF TMAP, atomic schema-3 push | `akdeniz:~/spotnight.py`, `~/spot_m2.stan`, `~/spotnight_cron.sh` | `production/akdeniz/` | **deployed 2026-11-24**, cron 13:00 local (LAN-only pull from `ganesha`) |
| Prospective matched A/B audit (Mertens linear B/G/R + enhanced display, sandboxed) | `pandr:~/monitor/spot_ab.py` | `production/pandr/spot_ab.py` | **deployed 2026-11-24**, runs nightly via `nightly.sh` |
| Photometry (tracks published centre + sunlit facade proxy `facade_med`) | `pandr:~/monitor/spotlab.py` | `production/pandr/spotlab.py` | deployed |
| Nightly maintenance (A/B audit, edge alarm, bracket pruning, ganesha backup, `apledger.py`, `web.log` rotation; retired `spotfit.R` removed) | `pandr:~/monitor/nightly.sh` | `production/pandr/nightly.sh` | **deployed 2026-11-24** |
| Synthetic end-to-end test (schema 3 CRF + ground-truth nulling < 0.5% across BGR) | `tests/test_synthetic_end_to_end.py` | `tests/test_synthetic_end_to_end.py` | **PASS** (`+0.36%, +0.22%, +0.36%`) |
| Offline July spatial MAP analysis (validation of centroid estimator) | Stan models + drivers | `spot_paper/code/` | offline analysis only |

## Retired components (removed 2026-11-24)

- `akdeniz:~/publish_live.py` — merged into unified `spotnight.py` (fixed `NMAX` slicing bug and winter fixed-`ref` Blue-clipping failure by stacking clean multi-bracket frames).
- `pandr:~/monitor/spotfit.R`, `spot_m2.stan` (and compiled unused Stan binaries `spot_m2`, `spotpos`, `spothier`) — replaced by the live vectorized 3-channel Stan M2 model on `akdeniz`.
- `akdeniz:~/refiner/{refiner.py,daily_refiner.sh}` and daily 50 GB Wi-Fi `flatfield_acc` rsync from `pandr` — retired.

## Live verification summary (2026-11-24)

- **Stan M2 (`akdeniz`):** `N=2921`, `rhat_max=1.0055`, `diag_ok=True`, `t=[0.9525, 0.9526, 0.9474]`, `k_fac=[+0.0042, +0.0035, +0.0072]`.
- **Multi-bracket TMAP (`akdeniz`):** `n_ch=[81, 98, 68]` clean frames/ch, `min_T=0.935`, `core_depth={b: 5.09%, g: 5.66%, r: 4.91%}`.
- **Prospective live `spot_ab.py` (`pandr`, `n=18` clean daylight triples):** Mertens linear `+4.15% → -0.12%` (`|after|` med `0.21%`, BGR `[-0.08%, -0.02%, -0.01%]`, `0/18` worse); enhanced display `+11.89% → +0.63%`.
