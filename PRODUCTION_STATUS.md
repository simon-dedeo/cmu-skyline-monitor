# Production status — window-spot correction system

Snapshot date: **2026-07-28** (post-review revision r2: frequency-split anti-inverse-spot cap + adaptive closed-loop gain solve in all three correction paths; the 1125 archive frames installed earlier on 07-28 were produced with the pixelwise cap and carry its quiet-disc signature -- safe, re-runnable on request).
This table is the implementation-status ground truth for `spot_paper/SPOT_FIT.pdf`;
the paper's claims should be read against it. `production/` contains byte-copies of the
files as deployed on each host at the snapshot date; the live hosts remain
authoritative.

| component | file (deployed host) | snapshot copy | status |
|---|---|---|---|
| Live corrector: map placement, velocity extrapolation from data epoch, persistent template lock (`.spot_lock.json`), ambiguity gate, amplitude self-cal, local-sky cap, bundle integrity check, staleness tiers | `pandr:~/monitor/flatfield.py` | `production/pandr/flatfield.py` | **deployed 2026-07-28** (five same-day revisions; backups `flatfield.py.bak-*` on host) |
| Fusion + enhance (correction applied pre-fusion) | `pandr:~/monitor/hdrfuse.py` | `production/pandr/hdrfuse.py` | deployed (unchanged since 07-20) |
| Nightly tracker: recursive prior, pinned-across-brackets centroid estimator, data-epoch state, px/day velocity, acceptance gate w/ run-day-first validation | `akdeniz:~/spotnight.py` | `production/akdeniz/spotnight.py` | **deployed 2026-07-28**, cron 13:00 EDT |
| Publisher: registered linear tmap at tracked centre, blemish-local sigma-clipped rings, refuse-to-publish gates, schema v2 + sha256, atomic push | `akdeniz:~/publish_live.py` | `production/akdeniz/publish_live.py` | **deployed 2026-07-28**, runs after nightly tracker |
| Legacy refiner (fallback tracker + scurve producer; pandr push **disabled**) | `akdeniz:~/refiner/refiner.py` | `production/akdeniz/refiner/refiner.py` | deployed, demoted 2026-07-28 (recursive anchor patch) |
| Legacy nightly analysis (M1/M2 photometric record, research per-pixel maps) — feeds nothing live | `pandr:~/monitor/nightly.sh`, `spotfit.R`, `spotmap.py` | `production/pandr/` | deployed, research-only; `spotmap.py` crop-coordinate bug fixed 2026-07-28 |
| Photometry (spot patch tracks published centre since 2026-07-28) | `pandr:~/monitor/spotlab.py` | `production/pandr/spotlab.py` | deployed |
| Offline archive reprocessing (closed-loop display-space gain + per-tick lock) | `akdeniz:~/backfill.py` | `production/akdeniz/backfill.py` | run 2026-07-28: 1373 ticks, installed to `pandr:~/monitor/archive/` (originals in `archive_precorrect/`) |
| Offline spatial MAP analysis (elliptical Student-t blob; validates the production centroid estimator) | Stan models + drivers | `spot_paper/code/` | offline analysis, not the nightly estimator — see paper §2.2 |

## Known gaps (honestly)

- No unified versioned bundle beyond schema v2 + array hash; full manifest (per-array
  hashes, training-day list, validation metrics) is future work.
- Escalation after repeated gate failures writes log + `ALERT` semantics only; no alert
  transport (email/notification) is wired.
- No CI; `tests/` contains the synthetic end-to-end test and the dipole metric; the
  review's fuller test matrix (edge crops, injected motion, corrupt bundles, lock
  distractors) is future work.
- `monitor-pandr/` in this directory is a **stale 2026-07-21 mirror** kept for history;
  do not review it as current. Current code is `production/`.

## Evidence files

- `spot_fit_check/sweep2.csv` — offline Table-2 source (filters disclosed in paper).
- `spot_paper/evidence/live_paired.csv` — machine-readable live paired measurements.
- `spot_fit_check/check.html`, `spot_fit_check/sweep.csv` — earlier diagnostics.
