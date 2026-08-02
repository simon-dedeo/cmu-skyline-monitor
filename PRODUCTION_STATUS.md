# Production status — window-spot correction system

Snapshot date: **2026-07-28** (post-review revision r3: scene-referenced caps REMOVED from all three correction paths (they eat the correction when bright cloud passes behind the blemish, and the pixelwise variant flattened noise); safety is model-referenced -- closed-loop gain clamped (0.4,1.8), live amplitude trim [0.75,1.35]x model, exposure scale <=2.5, total deficit floored at T>=0.85. The 1125 archive frames installed earlier on 07-28 predate this (pixelwise-cap era) -- safe, re-runnable on request).
This table is the implementation-status ground truth for `spot_paper/SPOT_FIT.pdf`;
the paper's claims should be read against it. `production/` contains byte-copies of the
files as deployed on each host at the snapshot date; the live hosts remain
authoritative.

| component | file (deployed host) | snapshot copy | status |
|---|---|---|---|
| Live corrector: map placement, velocity extrapolation from data epoch, persistent template lock (`.spot_lock.json`), ambiguity gate, amplitude self-cal, bundle integrity check (schema + hash + finiteness), per-tick provenance log (`spot_provenance.csv`), staleness tiers | `pandr:~/monitor/flatfield.py` | `production/pandr/flatfield.py` | **deployed 2026-07-28** (five same-day revisions; backups `flatfield.py.bak-*` on host) |
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

## Referee 2 (2026-07-28b) fixes

- `publish_live.py` velocity now divided by the elapsed epoch interval (px/day, was
  raw px/step); bundle carries `velocity_units` and `velocity_epochs`. **Deployed to
  akdeniz 2026-08-02** (backup `akdeniz:~/publish_live.py.bak-preveldeploy-20260802`);
  the deploy had been waiting on the transport outage recorded below, so every bundle
  published before that date carries raw px/step velocity — for the nightly cadence
  that actually ran, the two differ by the fit-epoch interval's departure from 1.0 d
  (~11% on the last state pair).
- `flatfield.py` now rejects unsupported `schema` values on load and appends per-tick
  provenance (model timestamp, tmap hash, code version, `.spot_apply_on` state, lock
  diagnostics) to `spot_provenance.csv`. Deployed to pandr 2026-07-28.
- `acceptance.gain` in the bundle is the **display-space closed-loop gain** (median of
  per-tick fusion-nulling gains, expected 2--3.5; compensates enhancement
  amplification). It is NOT the model-relative live clamp ([0.4, 1.8]) — the 3.323 in
  the checked-in bundle is in range for its actual semantics. Key renamed in docs;
  units documented here to resolve the referee's flag.
- Note: `.spot_apply_on` is an external enable flag; its per-tick state is now logged
  in the provenance file. The corrector ships disabled by default.

## Transport outage 2026-07-29 → 2026-08-02 (akdeniz could not reach pandr)

CMU wifi clients stopped being routable from the wired subnets, so every
akdeniz→pandr SSH returned "No route to host" (the name still resolves to
172.26.24.19). Both consumers fail soft and only log, so nothing alarmed:

- `daily_refiner.sh` frame rsync failed from 07-29 → `akdeniz:~/refiner/frames`
  froze at 07-28 (it kept refining stale frames and still supplied `scurve`).
- `publish_live.py` push failed 07-29/07-30; from 07-31 it refused earlier still
  ("only 0 usable frames/channel") because the frozen frame directory had aged out
  of its 2-day map window.

Net effect: `pandr:~/monitor/{spot_model.json,spot_tmap.npy}` stayed at the 07-28
bundle for five days while the tracked centre moved (1297.7,131.8) → (1317.6,119.0).
Velocity extrapolation held x to within a few px but ran ~15 px high in y, and the
bundle would have crossed `flatfield.py`'s >7-day staleness tier into fallback on
08-04. Capture, fusion, golden-hour publishing and the ganesha mirror were unaffected
throughout (they are outbound from pandr).

Fixed 2026-08-02 by routing akdeniz→pandr through the existing ganesha:2202 reverse
tunnel via an ssh_config alias on the original hostname — see
`production/akdeniz/ssh_config.pandr`. Frame directory resynced (bulk from the ganesha
mirror over wired, newest day from pandr). Pre-outage bundle preserved on pandr as
`spot_model.json.bak-stale20260728` / `spot_tmap.npy.bak-stale20260728`.

Residual gap: the silence itself. Both jobs still only log their failures, and the
nightly tracker's `ALARM`/`REJECTED` lines (the blemish is now ~119 px from the frame
top, so the background annulus can no longer be seated, and 08-01 acceptance regressed
to 0.84 removal) are likewise log-only. No alert transport is wired — see Known gaps.
