# Raw data sample

- `20260725_231728/` — the three raw exposure brackets (lossless PNG, E11/E44/E176 ms)
  behind Figure 1 of the paper: fuse them uncorrected to reproduce the left panel,
  or through `production/pandr/flatfield.py` + `hdrfuse.py` with the bundle below to
  reproduce the right.
- `spot_model.json`, `spot_tmap.npy` — a production model bundle (schema 2) as
  published to the capture host (paired: the JSON carries `tmap_sha256_16` of the
  `.npy`).
- The full bracket archive (~65 GB and growing, 5-minute cadence since 2026-07-20)
  does not fit in a git repository; it is available on request (sdedeo@andrew.cmu.edu).
