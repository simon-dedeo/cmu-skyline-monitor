# CMU Skyline Monitor — TODO (new machine `pandr`)

*Written 2026-07-17, the day the monitor was migrated from the old 2015 MacBook Pro
(`laboratorys-mbp`, camera hardware-dead) onto a MacBook Air M1 (`pandr`). This file
is BOTH a task list and an onboarding doc — a fresh Claude Code instance should be
able to read Part 0 and start working. The old machine's detailed history lives in
`TODO.md` (kept for reference); project memory (`lsm-macbook-monitor.md`) has the
full multi-week story. Migration writeup: `NEW-MACHINE.md`.*

**TL;DR status:** System is live and healthy on `pandr`. It serves the public site
(weather + last golden image) from a modern, fast machine. **There is no camera
attached yet** — Simon is buying a USB-3 4K unit. Everything camera-related fails
soft and the dashboards honestly show "camera down." The big new opportunity is
that this machine has ~10× the image-processing headroom of the old one (see Part 2).

---

## Part 0 — Orientation for a new Claude (read this first)

**Access.** `ssh proofsandreasons@pandr.wifi.local.cmu.edu` — passwordless key
login AND passwordless sudo. macOS 15.5 Sequoia, Apple M1, 8 GB RAM, 460 GB disk
(~390 GB free). Auto-login is ON; the machine is a dedicated always-on kiosk.

**Toolchain (NOT MacPorts — that was the old machine).**
- Homebrew at `/opt/homebrew` (`imagesnap`, `libusb` installed here).
- Python venv at **`~/monitor/venv`** — Python 3.13, OpenCV 5.0, numpy 2.5, libusb1.
  Every script's shebang / `PY=` / `IMAGESNAP=` points here. Run things as
  `~/monitor/venv/bin/python3 …`.

**What runs (launchd LaunchAgents in `~/Library/LaunchAgents/`, loaded in gui/501):**
| Job | Cadence | What it does |
|---|---|---|
| `com.lsm.monitor` | 300 s | `tick.sh` → ssh-localhost hop → `capture.sh` → `update.py`: weather, solar, system health, camera frames; archives; science log; uploads to santafe (~15 min throttle) |
| `com.lsm.goldscan` | 150 s | `goldtick.sh`; no-op outside golden windows, captures/scores the sky peak inside them, uploads once at window close |
| `com.lsm.monitor-web` | — | localhost:8787 dashboard + aimer web server (KeepAlive) |
| `com.lsm.kiosk` | at login | opens the dashboard in TRUE fullscreen via Google Chrome `--kiosk` (no Accessibility/TCC needed; Safari was dropped because its fullscreen keystroke is blocked for automation) |

**Health check (paste this):**
```
launchctl list | grep lsm                                    # want all 4
tail -30 ~/monitor/monitor.log                               # recent ticks
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/dashboard.html
~/monitor/venv/bin/python3 ~/monitor/update.py               # one manual tick
```
Dashboard footer light: **green = camera delivered a recent picture**, red = camera
down, amber = monitor unreachable. Right now it is correctly RED (no camera).

**Santafe upload.** `~/.ssh/id_ed25519` authorizes BOTH the localhost camera hop
AND santafe (`simon@santafe.santafe.edu:~/html/` → https://sites.santafe.edu/~simon/).
NB: the OLD machine used `id_rsa` for santafe; here it's `id_ed25519`. The public
dashboard/homepage also live at https://proofsandreasons.io/ (repo at
`/Users/simon/Desktop/PROOFS/proofsandreasons.github.io`).

**Load-bearing gotchas (learned the hard way — don't rediscover them):**
- **Camera + launchd = TCC-denied.** macOS grants camera access to the ssh login
  path, not to launchd/bash. So capture re-enters via `ssh localhost` (see `tick.sh`)
  to run under `sshd`. When a real camera is attached, its first access under sshd
  may still need a one-time camera grant to `/usr/libexec/sshd-keygen-wrapper`
  (GUI/console step — can't be done headless).
- **Never put scripts or data under `~/Desktop`** — it's TCC-protected; launchd gets
  "Operation not permitted." Everything lives in `~/monitor`.
- **`log` unsudo'd in zsh is the shell builtin** (useless) — always `sudo log show …`.
- zsh aborts on unmatched globs and does not word-split unquoted `$(…)` like bash;
  when scripting the remote, prefer writing a `bash` script file and running it.

**Key files in `~/monitor`:** `expose.py` (camera capture + HDR + exposure metering,
the heart of it), `update.py` (data.json generator), `capture.sh`/`tick.sh` (the
5-min tick), `goldpeak.py`/`goldscan.sh`/`goldtick.sh` (golden-hour scoring),
`science.py` (per-tick metrics → science.csv), `solar.py` (sun/golden times),
`aim.py` (+ aim.html, camera aiming UI), `roi_overlay.py`, `drift.py`, `dashboard.html`.
Old-machine data (old-camera frames, old science.csv) is parked in
`~/monitor/OLD-MACHINE-DATA-2026-07-17/` — **useful test data for prototyping** (see
Part 5). Adapted-code mirror on Simon's laptop: `/Users/simon/Desktop/MBP/monitor-pandr/`.

---

## Part 1 — System software updates (assessment)

**STATUS 2026-07-17:** Safari 26.5.2 **applied**; ffmpeg **installed**; light interface
trim **applied** (Spotlight index off, media/photo-analysis daemons off, reduce
motion/transparency, Siri off). macOS 15.7.7 is **downloaded + staged but NOT
installed** — Apple Silicon returns "Failed to authenticate" for a headless
`softwareupdate -i --restart` (needs volume-owner/login-password creds that
passwordless sudo doesn't grant). **To finish it: System Settings → General →
Software Update → Restart Now** (fast, already downloaded). macOS 26 Tahoe held.

Pending (`softwareupdate -l`), current OS is macOS **15.5 (24F74)**:
1. **Safari 26.5.2** — DONE.
2. **macOS Sequoia 15.7.7** (security, same major, needs restart) — **RECOMMENDED.**
   Low-risk point update; important for an always-on, network-reachable machine.
3. **macOS Tahoe 26.5.2** (MAJOR upgrade to macOS 26) — **HOLD / discuss first.**
   A major-version jump can change TCC/camera-permission behavior, launchctl domain
   semantics, and Homebrew/OpenCV-wheel compatibility — exactly the machinery this
   project leans on. Not worth the risk while we're mid-camera-bringup.

**Before any restart:** auto-login is already ON, so the LaunchAgents reload at login
— but verify after reboot with the health-check block above. Do it during a quiet
window. A restart will drop the SSH session; the machine comes back on its own.
Nothing else is out of date (`brew outdated` clean; venv fresh).

*Ask Simon before applying — a reboot interrupts the live site briefly. The security
update (15.7.7 + Safari) is the sensible baseline; skip Tahoe for now.*

---

## Part 2 — More image processing (the M1 headroom)

The old machine was a 2015 dual-core Intel; this is an 8-core M1 with an 8-core GPU,
16-core Neural Engine, and Metal 3. Measured on `pandr`:
- OpenCV 5.0 built with **NEON / NEON-FP16 / NEON-DOTPROD SIMD, GCD parallelism,
  OpenCL, and the Accelerate framework**; numpy uses **Accelerate BLAS**.
- **A 5-frame Mertens HDR fuse at full 4K (3840×2160) takes ~1.1 s.** The old machine
  did this at 1200p in many seconds. HDR, metering, drift phase-correlation, and the
  science metrics are now effectively free.

**The one real constraint is 8 GB RAM** (unified with the GPU; a couple of 4K frames
+ OpenCV buffers + Safari + Python adds up, and there are already minor pageouts).
Favor streaming/tiled processing over holding many full-res frames in memory; watch
`memory_pressure`. This rules out big in-RAM deep-learning models but leaves plenty
of room for classical CV and small/quantized ML.

**Things newly practical here (rough priority):**
1. **[DONE — ffmpeg 8.1.2 installed]** VideoToolbox HW encoding (h264/hevc/prores) and build
   the timelapse movie ON the machine (`h264_videotoolbox`/`hevc_videotoolbox`),
   instead of shipping frames elsewhere. Fast, and enables a daily auto-rendered
   day/golden movie. *(Not installed yet.)*
2. **Richer HDR** with the 4K cam: more brackets, per-region tone-mapping, luminance-
   only fusion + de-ghosting for moving clouds — all cheap now. Revisit the
   "is HDR worth it?" question (Part 3) with real headroom instead of a time budget.
3. **Proper cloud segmentation** to replace the crude "whitish-% of the top band"
   `cloud_frac` proxy in `science.py` — e.g. a red/blue-ratio sky/cloud classifier,
   or a small ONNX/CoreML segmentation model run via the Neural Engine. Calibrate
   against Open-Meteo `cloud_cover` (already logged side-by-side).
4. **Learned golden-hour detector** (detector v2, Part 3): with the archived scored
   frames we can fit a real model (sun-elevation + hue-excursion + contrast features →
   learn-to-rank), and even run a tiny CoreML model per frame. M1 makes training-lite
   and inference trivial.
5. **Optical-flow cloud motion / sky-condition trends**, night denoising / stacking
   for the dark sky cam, and object/event detection in the courtyard view (birds,
   planes, foot traffic) — all classical-CV-feasible now; pick what's scientifically
   interesting to Simon.
6. **Higher capture cadence / resolution** generally — the 5-min tick and 2.5-min
   golden scan were partly paced to spare the old machine and the flaky camera. With a
   healthy USB-3 cam and this CPU, faster cadence or full-res archiving is on the table
   (disk is the limit, not CPU: ~390 GB free).

Whatever we add, keep the fail-soft discipline (a hiccup never aborts a tick) and the
sparse-upload rule (Simon: "don't upload too much").

---

## Part 3 — Carried-over work items (from the old TODO, still relevant)

Most of the *analysis* items need fresh camera data, so they wait for the new camera;
the *build/prototype* items can start now against archived frames (Part 5).

**Camera-dependent (resume once the new cam is capturing):**
- **Golden-hour picks vs Simon's taste.** Ground truth: evening peak ≈ 8:20 PM EDT
  (maybe later), morning ≈ 6:10–6:15 AM EDT. Rebuild review panels over new windows,
  mark real peaks. The current pick rule is "frame nearest sun elevation −1°"
  (`goldpeak.py TARGET_ELEV=-1.0`); blend + all stats still logged for comparison.
- **`sky_ref` elevation-table tuning** (`expose.py elev_table`): daytime rows are
  verified; dawn/dusk rows (90/120/400/night 1500+gain20, in 0.1 ms units) are seeded
  guesses — check `science.csv` `ref_exp`/`sky_hex` through twilight; ref frames
  should be neither clipped nor black. (These are for the OLD sensor; **re-derive for
  the new camera.**)
- **WB drift check** — same sun-elevation + weather on different days → same `sky_hex`?
- **Single vs adaptive HDR — worth it?** Diff `HHMM_sky.jpg` vs `HHMM_sky_std.jpg`;
  count actual fuses. Now revisit with the M1's headroom (Part 2).
- **science.csv trends**: cloud% vs Open-Meteo (calibrate), GCC green-up, lit-windows
  overnight, twilight colour path, campus colour day-shape.

**Buildable now / soon:**
- **Detector v2 (sun position + image hue).** Best lead from the old data (83 scored
  frames, one hazy day): human-golden frames are **sky-hue excursions from blue**
  (327° magenta at dawn, 259° violet at dusk), and a **sun-elevation prior** alone
  moves picks into Simon's bands. Candidate `v2c = sat × bright-gauss ×
  (1+hue_exc/30) × elev-prior`. Gate the hue-excursion by OKLab chroma (hue-of-mean is
  unstable near grey). Weights are unfit — needs multi-day data. Prototype now on the
  archived panels (Part 5), lock later.
- **Corruption gate for goldpeak** (a torn frame once scored highest and became the
  featured image). Cheap + high value: horizontal-banding detector (row-to-row
  high-freq energy) or reject a pick whose score > 2× the window's running median.
- **Timelapse movie** from the 5-min archive (`archive_ref/` fixed-exposure series
  should be the smoothest) — see Part 2 item 1 (install ffmpeg first).
- **"Cited by" widget for Templeton grant 63750** on the homepage — OpenAlex, a daily
  GitHub Action committing `citing.json`, manual-approve queue. Fully independent of
  the camera; can be built anytime. (Details in `RESUME.md`.)
- **Courtyard (2nd) camera**: the setup already soft-supports a second cam
  (`update.py CAMERAS`, `aim.py` optional). If Simon wants a courtyard view again,
  wire the new second cam in; a powered hub is near-mandatory for two cameras.

---

## Part 4 — When the new USB-3 camera arrives (checklist)

**Camera: Elgato Facecam 4K** (4K60 UVC webcam, USB-C 3.0, Sony STARVIS 2 sensor,
fixed-focus glass — good for the distant skyline at infinity; DSLR-like manual controls
via Elgato Camera Hub, settings stored ONBOARD so a fixed exposure persists headless).
Delivers **uncompressed** (YUY2/NV12, 8-bit) → clean data for HDR fusion / denoise, no
MJPEG artifacts. **Two things to TEST on arrival:** (1) does macOS/OpenCV/AVFoundation
let us set exposure PROGRAMMATICALLY per-frame (needed for HDR exposure brackets)? — if
only onboard fixed exposure is settable (via Camera Hub GUI), true multi-exposure HDR
may be limited to a single manual exposure; (2) at 5 Gbps USB3, uncompressed 4K is likely
4K30 (irrelevant for 5-min stills, but confirm the mode). See storage sizing at the end.

Full version in `NEW-MACHINE.md`. Short form:
1. Plug in. Identify it:
   `system_profiler SPUSBDataType` and
   `~/monitor/venv/bin/python3 -c 'import usb1; [print(hex(d.getVendorID()),hex(d.getProductID()),d.getProduct()) for d in usb1.USBContext().getDeviceList()]'`
2. Update vendor/product IDs, resolution, and the exposure/HDR config in `expose.py`
   (old GS sky cam was `0x0c45:0x0578`, now dead). Update the imagesnap `-d "Arducam …"`
   device-name strings in `expose.py`/`update.py`. Re-derive the `elev_table` for the
   new sensor.
3. **Camera TCC grant**: first access runs under `sshd` (localhost hop). If frames are
   silently denied, open the camera once from a console Terminal / approve the prompt.
4. Aim: `aim.py` (served at http://127.0.0.1:8787/aim.html) — pass a big `LIMIT`
   (e.g. 3600) so it doesn't auto-stop mid-session. Then verify ROIs with
   `roi_overlay.py` and pin a fresh `.drift_baseline.jpg`.
5. Prefer a **powered USB hub with real per-port power switching** (uhubctl list) —
   the old machine's firmware wedges always needed a physical replug because the cheap
   hub's "per-port power" was fake. A modern USB-3 cam should be far less finicky.
6. Watch one full tick (`tail -f ~/monitor/monitor.log`) and confirm the dashboard
   light goes green.
7. **Aim-drift check (ONGOING — don't skip).** After the mount is set and the baseline
   pinned, run `~/monitor/venv/bin/python3 ~/monitor/drift.py` (no args — it phase-
   correlates the building band of each `archive_ref` frame against the pinned
   `.drift_baseline.jpg` and reports px drift over time). Check it in the first days
   (the old 2015-machine mount visibly settled/crept for hours after each re-aim) and
   periodically after. **If drift exceeds ~40 px:** stiffen/adjust the mount, re-aim
   (step 4), re-run `roi_overlay.py` to re-verify the science ROIs, re-pin
   `.drift_baseline.jpg`, and re-do any landmark pose calibration. A slow creep that
   plateaus is normal settling; a persistent walk means the mount needs stiffening.

---

## Part 5 — Safe things to play with NOW (no camera needed)

A fresh Claude can experiment without waiting for hardware:
- **Weather/data pipeline & dashboards** are fully live — safe to iterate on
  `dashboard.html`, `update.py` fields, the public homepage.
- **Archived old-camera frames** for CV/detector prototyping live in
  `~/monitor/OLD-MACHINE-DATA-2026-07-17/` (old `archive/`, `archive_ref/`,
  `golden_candidates/`) and, on Simon's laptop, `/Users/simon/Desktop/MBP/golden_panels/`
  (panel tooling: `panel_scan.py` runs on the machine, `build_panels.py` builds the
  review artifact locally). Two good golden windows survive (7/15 evening, 7/16
  morning) with 2.5-min frames — enough to prototype detector v2 and the corruption
  gate end-to-end against real frames.
- **Benchmark / feasibility**: the M1 makes it cheap to try heavier pipelines on those
  archived frames (multi-scale HDR, cloud segmentation, optical flow) and measure
  cost before committing them to the live tick. Mind the 8 GB RAM.
- **Install ffmpeg** and render a timelapse from the archived frames as a first proof
  of the on-machine movie pipeline.
- Keep changes to the live tick behind the same fail-soft pattern; test scripts
  against archived frames first, not the production capture path.

---

## Part 6 — Research ideas (parked)

Vision/science R&D ideas (cloud-motion→wind, turbulence, cloud-type, altitude,
pedestrian dynamics, camera-pose calibration) are **parked in `BRAINSTORM.md`**
until we've accumulated data with the new camera. Nothing there is on the setup
critical path — revisit once the camera is running.

---

## Part 7 — Storage sizing for 4K archiving (Facecam 4K, every 5 min)

Measured 4K UHD (3840×2160) file sizes on `pandr`, for both a saved **reference** frame
and a saved **HDR** display frame per 5-min tick (288 ticks/day). Disk = **371 GB free**.
**KEY REFRAME: uncompressed TRANSFER ≠ uncompressed STORAGE.** Grab/process frames
uncompressed in RAM (the win for HDR fusion + denoise), then SAVE the result compressed —
you keep the quality without the storage blow-up. (Facecam is 8-bit YUY2, so 8-bit
lossless PNG already preserves everything the sensor sends.)

Per-image (daytime, high detail): JPEG q95 **~1.3 MB** · PNG lossless **~5 MB** ·
uncompressed 8-bit **24.9 MB** · uncompressed 16-bit **49.8 MB**. Night frames are ~5×
smaller for compressed formats (dark → cheap), unchanged for uncompressed. NB these were
measured on upscaled 1200p frames — a true 4K sensor has more high-freq detail, so bump
compressed sizes ~1.3–2× in planning (JPEG ~2 MB, PNG ~10 MB); uncompressed is exact.

Two images/tick, 288/tick/day, ~time to fill 371 GB (planning numbers, true-4K adjusted):
| policy (ref + HDR) | per day | per year | fills 371 GB in |
|---|---|---|---|
| both JPEG q95 (~2 MB ea) | ~0.9 GB* | ~275 GB | ~1.3 yr |
| **PNG ref + JPEG HDR (~10 + 2 MB)** | ~2 GB* | ~600 GB | **~6 mo** |
| both PNG lossless (~10 MB ea) | ~3 GB* | ~1 TB | ~4 mo |
| both uncompressed 8-bit (24.9 MB ea) | 14 GB | 5.2 TB | **~26 days** |
| ref uncompressed 16-bit + HDR uncmp 8-bit | 21 GB | 7.8 TB | ~17 days |
*compressed rows: real 24/7 totals are ~half (nights cheap); uncompressed rows are firm.

**Recommendation:** reference = **lossless PNG** (~10 MB, preserves all sensor bits for
science), HDR display = **JPEG q95** (~2 MB, it's for display; save 16-bit PNG only if you
want to re-tone-map later). That's the bolded row: sustainable for ~6 months, then apply a
**retention policy** for indefinite running — e.g. keep full-res last N days, thin older
days to JPEG-only or hourly, and/or auto-render a daily timelapse (ffmpeg) then drop the
source frames. **Saving uncompressed every tick is NOT viable** (fills the disk in weeks);
only keep uncompressed brackets for short, deliberate study campaigns.

### DECISION (Simon 2026-07-17)
- **Save format: PNG reference frame + JPEG HDR frame, every 5 min.** (The bolded row.)
- **Revisit the whole storage question ~2026-08-17** (one month), once we see the real
  daily footprint from the actual Facecam frames.
- **Auto space-management default (implement when archiving starts):** a watchdog checks
  free disk space; **when free space drops below 50 GB, convert the OLDEST reference PNGs
  to JPEG (oldest-first) until at least 100 GB is free again.** This reclaims ~8 MB per
  frame (PNG ~10 MB → JPEG ~2 MB) while keeping recent frames full-res for science, and
  it self-throttles (stops as soon as 100 GB is free). Runs unattended — no manual pruning.

  Implementation sketch (add at camera setup, not needed until then): a small periodic job
  (daily launchd, or a check folded into `capture.sh`) —
  `free=$(df -g / | awk 'NR==2{print $4}')`; if `free < 50`, walk `archive_ref/` oldest→
  newest converting `*.png`→`*.jpg` (`cv2`/`sips`), deleting the PNG, re-checking free
  space after each and stopping once `free ≥ 100`. Log how many were converted. Keep it
  fail-soft and idempotent. (460 GB disk, ~371 GB free now, so 50/100 GB are safe floors.)

---

## Appendix — job control cheatsheet
```
UID=$(id -u)
launchctl kickstart -k gui/$UID/com.lsm.monitor     # force a tick now
launchctl bootout   gui/$UID/com.lsm.<job>           # stop a job
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.lsm.<job>.plist   # (re)load
```
Old machine (`laboratoryforsocialminds@laboratorys-mbp…`) is a warm spare; its jobs
are parked in `~/Library/LaunchAgents/disabled-by-claude-2026-07-17/`. Don't run both
machines' jobs at once — they'd both upload to santafe.
