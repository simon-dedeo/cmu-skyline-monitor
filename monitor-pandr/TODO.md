# CMU Skyline Monitor — TODO

*Written 2026-07-14, after the camera-wedge incident and the same-day fixes. Boot
this up **~2026-07-17 or later** — by then several golden hours of new data will
have accumulated under the new setup. Companions: `RESUME.md` (system detail +
health-check commands), `monitor/GOLDEN.md`, project memory.*

**Quick health check when you sit down:** `launchctl list | grep lsm` (want
monitor + goldscan + monitor-web) · `tail -30 ~/monitor/monitor.log` · dashboard
footer light: **green = camera took a picture recently** (red = camera down,
amber = monitor unreachable).

---

## ⚠️ STATUS 7/16 evening (~7 PM EDT) — SKY CAMERA IS DEAD (hardware). READ THIS FIRST.

**The B0578 GS camera's USB controller died at 3:04 PM EDT 7/16 and is not coming
back.** Confirmed by on-site isolation testing (Simon + Claude, ~6:45–6:55 PM):

- camera → hub → Mac: fails · camera → extension → Mac: fails ·
  **camera → Mac DIRECTLY: fails** — presence detect OK, then a
  "failed to get device descriptor" retry loop every ~6 s, on every port tried
  (HS01/SSP1/SSP2/HS05); kernel eventually disables the port
  ("persistent enumeration failures"). With every intermediary eliminated, the
  fault is inside the camera. No software or cable fix exists.
- Death sequence (unified log): flapping against the hub from 2:55 PM
  (`createDevice 0xe00002bc` + pipe stalls), dragged the ENTIRE hub cascade off
  the bus 3:04–3:08 PM. Third failure in 3 days, each deeper (stream lockup →
  enumerates-but-no-frames → dead controller) = progressive hardware death,
  NOT duty cycle. §B's firmware-email/spare-B0578 items are moot for this unit.
- Hub + extension were never proven bad — the dead camera fails everywhere, so
  they're untested, not exonerated. 30-second bare-hub test when the new camera
  arrives.

**What this voids:** no golden windows, archive frames, or live science colour
from 7/16 19:04Z until a replacement is installed. The 17:55Z clean epoch got
~1 h of data. Week-run review expectations below are mostly moot; hours.html
holds at 2 pairs (7/15 evening + 7/16 morning — both good).

**State it's left in (verified, needs no babysitting):** weather/data.json/uploads
all ticking; science.csv running (colour columns stale-duplicated during the
outage — drop duplicate-colour runs post-hoc); watchdog correctly no-ops every
tick ("GS cam ABSENT on USB — skipping reset" in monitor.log, harmless); featured
image stays the 7/16 6:32 AM peak; **kiosk + public dashboard correctly RED:
"camera down · last picture 3:04 PM."**

**Status lights verified end-to-end 7/16 (green ⇔ camera actually delivered a
recent picture):** update.py writes `cameras.sky.last_capture` = sky.jpg mtime,
which freezes the moment capture fails. Public dashboard (PROOFS commit f329274,
pushed + live): green <45 min, red "camera down" beyond, amber "monitor offline"
if data.js itself goes stale. Kiosk: same scheme, 20-min threshold, local
data.json. Both verified live during this outage. Known leak: a data.js with no
`cameras` block at all (pre-7/16 format) falls back to plain green "system up".

**When you're back:**
1. **Order the replacement** (research done, in chat + memory): ELP/Arducam-class
   4K MJPEG-over-USB2, M12 fixed-focus lens — pair with a **powered hub that has
   REAL per-port power switching** (check the uhubctl compatibility list; the
   Genesys hub's "per-port power" proved ganged/fake on 7/16, which is why
   firmware wedges always needed a physical replug).
2. On arrival: bare-hub test (does the old hub still enumerate alone?), then
   extension retest, then Mac→hub→camera on an inner-chip socket if reusing the
   old hub.
3. Plug it in and ping Claude — config (product ID / resolution / exposure
   table), aim.py session, roi_overlay.py, and full tick verification are all
   doable remotely.
4. **Optional stopgap meanwhile:** the courtyard Arducam 1080P Low Light is still
   unplugged. Prop it toward the skyline and Claude can repoint the sky-camera
   config at it (different product ID + res; small change, verifiable remotely).

---

## A. Review once data has accumulated (the main event)

1. **Golden-hour picks vs Simon's taste.** Ground truth so far: evening peak
   ≈ **8:20 PM EDT (maybe a bit later)**; morning ≈ **6:10–6:15 AM EDT**. The
   scorer's evening picks bracketed but missed it (7:36 live, 8:51 retro-best).
   Since 7/14 every golden window archives 2.5-min frames (`archive/DAY/HHMM_gold.jpg`)
   — rebuild the review panels over a few new windows and mark the real peaks.
   (Panels tooling from 7/14 works well: score with `goldpeak.stats()`, grid by time.)

2. **Detector v2 — sun position + image hue** (Simon's hypothesis). Fit against
   the marked peaks: does (sun_elev, campus/sky hue) predict the chosen frame?
   Runner-up ideas in `RESUME.md` §TODO-2: warm-cloud illumination, raking-light /
   façade contrast, colour-temperature path, learn-to-rank on rated frames.

3. **Tune the `sky_ref` elevation table** (expose.py `elev_table`). The daytime
   rows verified good on 7/14 (sun 61° → exp 8 → p60 163, clip 0%). The dawn/dusk
   rows (90 / 120 / 400 / night 1500+g20) are seeded estimates — check
   `science.csv` `ref_exp` + `sky_hex`/`campus_hex` through a few golden hours:
   ref frames should be neither clipped nor black through twilight. Adjust rows,
   don't add metering.

4. **WB drift check.** The B0578 has no exposed white-balance control (assumed
   module-fixed). Verify: same sun elevation + similar weather on different days
   → same `sky_hex` in science.csv? If it drifts, colour comparability needs work.

5. **Single vs adaptive HDR — is HDR worth it?** Diff `HHMM_sky.jpg` (adaptive)
   vs `HHMM_sky_std.jpg` (single) across the week; count how often it actually
   fused (`hdr:` lines in monitor.log). Full notes + alternatives (luminance-only
   HDR, de-ghosting, quality gate) in `RESUME.md` §TODO-3.

6. **Self-healing audit.** grep monitor.log for `camera watchdog` and
   `pre-window camera reset` — did the watchdog ever fire in anger? Any recurrence
   of the wedge (SetPipePolicy errors in `log show`)? Confirm evening windows now
   run to ~9:09 PM (the UTC-midnight truncation is fixed) and that in-window ticks
   log golden-reuse rather than capturing.

7. **science.csv trends** (new schema since 7/14; old file = `science_v1.csv`):
   cloud% vs Open-Meteo cloud_cover (calibrate), GCC green-up, lit-windows
   overnight, twilight colour path — and the new campus colour's day-shape.

8. **Rebuild the timelapse movie** from the 5-min archive; the `archive_ref/`
   fixed-exposure series may make the smoothest (flicker-free) movie yet.

## B. Build items

- **Corruption gate for goldpeak** (from the 7/14 incident: a torn frame scored
  blend 1067 and won the window). Ideas: horizontal-banding detector (row-to-row
  high-freq energy), or reject blend > 2× the window's running median. Cheap and
  high-value — a garbage frame should never be the featured image again.
- **If camera lockups recur** (research 7/14): "stops streaming after a random
  time" is a documented Arducam UVC behavior class (sibling model B0574 has a
  support-forum thread for exactly this). Escalations: (a) firmware update —
  **support email drafted at `arducam_support_email.txt`, send it**; the burn
  needs Arducam's Windows-only tool + a firmware file they supply (nothing
  public for B0578); flash from any Windows laptop with the camera unplugged
  from the Mac (~5 min), read the version before/after, don't flash through a
  VM. (b) a **powered USB hub** — the module draws the full 500 mA a port can
  supply, and marginal power is a classic cause of UVC firmware flakiness —
  zero-risk, can do today. (c) order a **spare B0578** (~$60) — cheap insurance
  and an A/B test of whether this unit is marginal.
  **USB power audit (7/14):** the Mac's Type-A port hardware can source ~2.1 A
  (`kUSBWakePortCurrentLimit = 2100` on the root port), but each USB-2 device
  gets the standard 500 mA allocation — and the camera (which requests exactly
  500) sits behind a TWO-layer Genesys unpowered hub cascade (~100 mA/layer
  self-draw + voltage drop). Zero headroom. ~~Quick win: plug directly into the
  Mac~~ — **TRIED 7/14, DON'T: extension-direct wedges the firmware in minutes**
  (the hub's signal re-timing at the end of the extension run is load-bearing;
  see the note at the bottom). A powered hub WILL work (own 5 V rail + re-timed
  signal) and becomes near-mandatory when the courtyard cam returns (2 cams ×
  500 mA through one unpowered hub = well over budget — plausibly why concurrent
  access used to blow the courtyard to 255).
- **"Cited by" widget for Templeton grant 63750** on the homepage — OpenAlex via
  a daily GitHub Action committing `citing.json`; manual-approve queue. Full
  notes in `RESUME.md` §TODO-1.
- **Courtyard cam**: remount/replug, re-enable in update.py/aim.py (already
  soft-fail optional), eventually restore courtyard on the dashboards.
- **Kiosk autostart** (`kiosk.sh` exists; make it launch at login if wanted).

## C. System chores (full passwordless sudo since 7/14)

- **Security updates**: macOS 11.7.10 → 11.7.11 + Safari 16.6.1. Needs a restart —
  pick a quiet midday, verify auto-login first so the LaunchAgents come back.
- Optional: reboot clears the cosmetic 109-day-uptime systemstats noise (known
  false alarm, no urgency).

## Done recently (for orientation)

- **7/13**: golden overhaul (1-min scan, peak-at-close upload, hours.html), OKLab
  colour, adaptive HDR, science.py tier-0, sparse uploads, 2-line golden caption.
- **7/14 morning**: camera firmware wedge diagnosed (root cause: dawn duty cycle;
  `SetPipePolicy` lockup) — recovered by physical replug; corrupt 6:12 peak
  replaced with real 6:10 frame on the public site; **solar.py local-date fix**
  (evening windows no longer truncate at 8 PM); golden review panels artifact.
- **7/14 afternoon**: golden cadence → **2.5 min single-owner** (2 multi-exposures
  per 5 min in windows); all rapid scans archived; **camera watchdog + pre-window
  reset**; `usbreset.py`; full sudo granted; `pmset -a sleep 0`.
- **7/14 late**: **`sky_ref` fixed-settings reference frames** (sun-elevation-keyed
  exposure, every tick, archived to `archive_ref/`), colour bars read them;
  **campus colour** (rows 55–100%) replaces whole-image in the second bar AND in
  the golden caption (`campus_hue` in golden.js, pages fall back to `img_hue` for
  old files); science.csv rotated to the new schema.

## STATUS 7/15 (~09:45 EDT) — camera fixed, data reset, fresh start. READ THIS FIRST TOMORROW.

**Where things stand:** all healthy. 3 launchd jobs up, tick clean, kiosk verified
serving the live frame (byte-identical to disk, fresh, correct framing). **All data
series restart at 2026-07-15 13:43Z** — one continuous, discontinuity-free series
from here.

**LATE UPDATE (~09:50 EDT): camera RE-AIMED again** (aim had drifted after the
socket fix). After aiming: pipeline restarted (all 3 jobs unload/load), the handful
of frames + the one science.csv row captured under the morning's old aim swept to
`ARCHIVE-2026-07-15/old-aim-frames/`, so the live series is 100% new-aim. **ROI
bands re-checked against the new framing with a labeled overlay — all five land
correctly**: SKY box (rows 0–50 × cols 33–67) clean sky, bottom edge just grazes
the far roofline (<1% contamination, fine); CAMPUS band (55–100%) starts safely
below the skyline, no sky in it; FACADE band (52–82%) on the window façades;
LAWN/gcc box (66–82 × 30–95) is a lawn+green-roof/hardscape mix — same class mix
as before, fresh-epoch baseline anyway; CLOUD band top-40% includes the rotunda
tower as always (known proxy bias). Overlay tool: `~/monitor/roi_overlay.py`
(draws all five bands on the current sky.jpg) — reuse it after any future re-aim.

**DRIFT after the 7/15 re-aim:** camera held steady ~15 min post-aim, then a
discrete ~16 px slip at ~10:00 AM EDT (nose down + slight pan right), then
decelerating creep to −23 px vertical / −6 px horizontal, **flat (≈0 px/h) from
~1:45 PM onward — settled**. Full curve + method: drift artifact
(https://claude.ai/code/artifact/9c53b509-10cc-46f8-ad81-5a535e8d4f55). Bands
verified still valid at the settled offset.

## STATUS 7/16 (~11:45 EDT) — first two windows GOOD; camera DOWN again since 8:35 AM (needs replug)

- **Both golden windows ran perfectly** (first full test of goldscan@2.5-min):
  evening 7/15 (42 frames, 7:19–9:08 PM) and morning 7/16 (41 frames, 5:45–7:31 AM).
  Peaks finalized + uploaded (evening 8:47 PM, morning 6:32 AM); hours.html updated.
- **Camera wedged 12:35Z (8:35 AM EDT)** — ~1h AFTER the morning window closed, during
  normal 5-min ticks. Signature: enumerates clean (no "#2"), control transfers OK,
  zero frames at ANY resolution (FaceTime cam fine). Watchdog (killall+usbreset)
  fired 6+× harmlessly. **`hubpower.py` (true VBUS drop, 20 s off) did NOT recover
  it** — first wedge to survive a software power-cycle. **Needs physical replug**
  (keep it on an inner-chip hub socket). Until then: no golden frames (tonight's
  evening window will be missed), sky/sky_ref stale, science.csv colour columns
  stale-duplicated (drop duplicate-colour runs post-hoc), weather/data.json fine.
- **Golden-hour panels built from the two windows** (score curves + every frame +
  candidate scorers): https://claude.ai/code/artifact/1027f531-b57f-47eb-a903-09c91b54e960
  Key findings for detector v2 (TODO §A2): v1's evening drift past sunset is
  structural (sat rises monotonically at dusk); warm term ≈ useless (negative in
  79/83 frames); the human-golden frames show as SKY-HUE EXCURSIONS from blue
  (327° magenta 6:06 AM, 259° violet 8:39 PM); a sun-elevation prior alone
  (× exp(−((elev−2)/3.5)²)) moves picks to 8:39 PM / 6:16 AM — in/near Simon's
  remembered bands. Candidate v2c = sat × bright-gauss × (1+hue_exc/30) × elev-prior
  picks 8:39 PM / 6:06 AM. Weights are guesses from ONE hazy day — mark real peaks
  on the panels before locking anything.
- hubpower.py as watchdog rung 3: NOT wired in (it didn't fix this class of wedge;
  revisit after the replug).
- **ROOT CAUSE dug out of the unified log (7/16 ~13:30):** the camera died IDLE.
  The 12:35Z tick was textbook (sky HDR 281 frames, then sky_ref 51/51 frames,
  clean StopStream stats, client exit 12:36:03Z); then FIVE MINUTES OF NOTHING —
  no monitor process, no goldscan (window closed 11:31Z), no watchdog, zero
  kernel USB events, zero power events; at the 12:41:07Z open it was already
  brain-dead (control steps 4–5 s each vs <1 s healthy; 180 USB transactions,
  FrameCompleted: 0 at both 1080 and 1200). Nothing we ran triggered it →
  spontaneous firmware death while idle/suspended between ticks, the documented
  Arducam "stops streaming after a random time" class. Further kills the
  duty-cycle theory (survived the 2.5-min window, died 1 h later under 5-min load).
  - Watchdog usbreset re-enumerated the cam 10+× through the morning (enumeration
    always OK, streaming never returns). hubpower.py ran 3× (11:10/11:15/11:17 EDT;
    hubs + cam visibly re-enumerated each time) yet still 0 frames → likely the
    Genesys "per-port power" is ganged/fake (data path resets, VBUS at the camera
    never actually drops) — which is why only a physical replug clears firmware
    wedges. Strengthens §B: firmware email + powered hub with REAL per-port
    switching (check uhubctl compatibility list) + spare cam.
  - Log forensics gotchas: `log` unsudo'd is the ZSH BUILTIN (silently returns
    nothing useful) — use `sudo -n log show ...`. And `SetPipePolicy 0xe00002c2`
    appears on EVERY healthy StartStream on this port (chronic noise, fell back
    and streamed fine all week) — the real wedge signature is FrameCompleted: 0
    + sluggish control transfers, not that error line.

## STATUS 7/16 (~14:00 EDT) — camera BACK (new Mac port), picker now −1°, DATA EPOCH RESET

- **Simon physically replugged the camera ~13:40 EDT — into the OTHER Mac Type-A
  port** (deliberate A/B: does the port matter?). Topology verified healthy: root
  port moved 0x141→0x142, cam still behind the hub cascade on an inner-chip socket
  (hub→hub→cam @0x14244000). **Production recovered unaided on the 17:45Z tick**
  (fresh sky + sky_ref, live ref colour) — replug remains the only fix for this
  wedge class. Watch whether wedge frequency changes on this port.
- **Golden pick rule CHANGED (Simon, after reviewing the panels): the frame nearest
  sun elevation −1° wins, morning AND evening.** goldpeak.py: `TARGET_ELEV=-1.0`,
  pick score = −|elev−target| replacing blend as the running-max criterion; blend +
  all other stats still computed/logged (science, panels, golden.js caption).
  UPLOAD line format unchanged (goldscan parses positionally). Sandbox-tested on
  the remote (running-max plumbing correct). Every 2.5-min frame still archived.
  **Tonight's window = first live test: expect pick ≈8:52 PM (sun −1°), upload at
  window close ~9:09 PM.** v1-blend vs −1° comparison stays possible from the
  archived frames.
- **DATA EPOCH RESET #2 (Simon's call): everything moved to `~/monitor/ARCHIVE-2026-07-16/`**
  (archive/ 89M, archive_ref/ 28M, science.csv, skycolor.js, .skycolor_today.json)
  at 17:51:23Z, between ticks. Nothing deleted. **Clean epoch: 2026-07-16 ~17:55Z**
  — new port, new picker, one schema. KEPT in place (same as 7/15 reset): golden.js /
  goldindex / .golden_peak.json (public-site continuity), live state (.last_upload,
  .sky_ref_meta.json, .skyfail, state_sky.json, data.json), and `.drift_baseline.jpg`
  (aim unchanged by a plug swap at the Mac end — verified by drift.py post-restart).
- The 7/16 morning + 7/15 evening gold frames + panel data survive in
  ARCHIVE-2026-07-16/archive/ and locally (golden_panels/goldpanel/ + artifact).
- Camera replacement: Simon looking into a ~4K unit; suggestions given in chat
  (ELP/Arducam 4K MJPEG-over-USB2 M12 modules, Brio caveats, powered-hub-with-real-
  port-power pairing).

## WEEK RUN — started 7/15 19:59Z, review ~7/22 (supersedes "check tomorrow" below)

At ~16:00 EDT, once drift flattened, ALL settling-period data (13:43–19:55Z frames,
science rows, colour-bar state) was swept to `ARCHIVE-2026-07-15/settling-frames/`
and all three jobs restarted. **Clean week epoch: 2026-07-15 19:59Z.** Everything
archived since then is settled-mount, current-aim, single schema — one clean week
of data if the mount holds.

- **Drift baseline pinned: `~/monitor/.drift_baseline.jpg`** (= archive_ref/
  2026-07-15/1959.jpg; verified dx=dy=0, resp=1.0). `drift.py` (rewritten, in
  ~/monitor) defaults to comparing ALL archive_ref days against it — just run
  `/opt/local/bin/python3.11 ~/monitor/drift.py`. Confidence falls with
  sun-position mismatch: compare like-lit hours (~4 PM EDT frames) across days;
  ignore resp<0.03 rows.
- At review expect: ~130 ref frames/day × 7 days; drift flat (if it walked >~40 px:
  stiffen mount, re-aim, rerun roi_overlay.py, re-pin baseline); ~14 golden windows
  in goldindex/hours.html; science.csv one unbroken schema; watchdog silent in
  monitor.log; tonight (7/15 23:19Z window) was the FIRST full goldscan@2.5-min test.
- All §A review items (golden picks vs ground truth, detector v2, elev_table
  dawn/dusk tuning, WB drift, HDR worth-it, science trends, timelapse movie) run
  on this week's data.

**1. Camera outage (7/14 13:42 → 7/15 09:23 EDT) — RESOLVED.** It died 12 min after
the re-aim: the plug had moved to a different hub socket. **Hub-socket law: the hub
box contains THREE Genesys chips; sockets wired to the OUTER chip can't sustain the
camera's 1920×1200 high-bandwidth mode** (enumerates fine, streams ≤720p only,
`StartStream SetPipePolicy 0xe00002c2`). Simon moved it back to an inner-chip socket
→ production recovered unaided on the next tick. Not a cadence problem — don't slow
goldscan on this evidence. **Don't touch the camera's hub socket.**

**2. New recovery tool: `~/monitor/hubpower.py`** (sudo) — cuts real VBUS power to
the hub ports in software (uhubctl-style PORT_POWER off/on; verified: inner hubs
drop off the bus). A software replug — stronger than usbreset.py, no walk to campus.
Ladder is now: killall VDCAssistant → usbreset.py → **hubpower.py** → physical
replug. TODO: wire it in as rung 3 of the capture.sh watchdog.

**3. Data reset (Simon's call, ~09:30 EDT): everything pre-7/15 moved, nothing
deleted.**
- On the Mac → `~/monitor/ARCHIVE-2026-07-15/` (397 MB): frame archive/ +
  archive_ref/, science.csv + science_v1.csv, skycolor state, monitor/web/aim/
  experiment/timelapse logs, .golden_review panels, old peak JPGs, stale
  courtyard.jpg.
- Locally → `Desktop/MBP/ARCHIVE-2026-07-15/`: samples, exposure_compare,
  golden_candidates, timelapse mp4, dashboard preview.
- **Kept in place** (deliberate): golden.js / goldindex.js + state — public site
  keeps its featured image (7/14 6:10 AM) and hours.html history; live state files
  (.last_upload, .skyfail, .sky_ref_meta.json, state_sky.json, data.json). Ask
  Simon if the public golden history should be wiped too.
- Verified post-reset: first tick recreated science.csv (new-schema header, live
  ref colour), archive dirs, skycolor.js.
- Old-data caveats now moot for analysis (outage gap, re-aim discontinuity, stale
  science rows all live in the archive folder, not the live series).

**4. Check tomorrow (7/16):**
- Did **tonight's evening golden window (7:19–9:09 PM EDT)** run? It's the FIRST
  real test of goldscan@2.5-min + reference frames + campus colour. Look for:
  `archive/2026-07-15/HHMM_gold.jpg` frames, a new featured image on the public
  dashboard (evening peak, not 7/14 morning), hours.html gained today's pair,
  `.golden_peak.json` window_id `2026-07-15_evening`.
- This morning's window should also have run (~5:45–6:45 AM) — same checks, morning.
- Watchdog: `grep "camera watchdog" ~/monitor/monitor.log` — should be SILENT. Any
  fire = the socket/cadence question reopens.
- science.csv filling at 5-min cadence with `[ref exp=...]` rows (not "NO ref");
  sky_ref exposure stepping through the elev_table at dusk (rows 90/120/400/1500
  are unverified seeds — see §A3).
- Golden picks vs Simon's ground truth (evening ≈8:20 PM, morning ≈6:10–6:15 AM).

Original incident notes below (diagnosis detail).

- Sky cam stopped delivering frames **12 minutes after the 13:30 re-aim** on 7/14 —
  before the evening golden window. **No golden frames from 7/14 evening or 7/15
  morning**; archive/2026-07-15 and archive_ref/2026-07-15 are empty. Featured
  image still the 7/14 6:10 AM peak. Weather/science kept running (science.csv
  colour columns are stale-duplicated for the gap — drop those runs).
- Watchdog fired 25× (killall + usbreset) — couldn't fix it. **New tool that CAN
  drop VBUS in software: `~/monitor/hubpower.py`** (sudo; uhubctl-style
  CLEAR/SET PORT_POWER on the Genesys hubs — inner hubs visibly drop off the bus,
  so it's a true power-cycle, a software replug). After it, the camera boots and
  streams — but **only up to 1280×720**; 1920×1200 AND 1920×1080 won't negotiate
  (`StartStream SetPipePolicy 0xe00002c2` persists, even after a 20 s off-time).
- **Topology changed during the re-aim**: the cam now sits on the OUTER hub chip;
  before 7/14 it was behind the two-layer cascade. Prime suspect: the plug moved
  to a different (signal-marginal) hub port. **Fix needs hands: move the camera
  plug back to its original hub port** (or, better, the powered hub from §B).
  Software power resets alone don't restore full-res on this port.
- Cadence note: this lockup is NOT the goldscan duty cycle — it happened outside
  any golden window, right after the aim.py streaming session + port move.
- After replugging: watch one 5-min tick (`tail -f ~/monitor/monitor.log`) —
  the watchdog/production path recovers on its own once full-res works. Consider
  adding `hubpower.py` as rung 3 of the capture.sh watchdog ladder.

## Note for the week review (added 7/14 ~13:35 EDT)
- **Camera was re-aimed 7/14 ~13:30 EDT** (after the replug adventures): framing now
  slightly lower/wider on campus than before. All ROI-dependent science.csv series
  (gcc, cloud_frac, lit_windows) have a discontinuity at that timestamp — treat
  pre/post separately, and re-check the ROI boxes against the new framing at review
  (sky region + campus row 0.55 verified still valid by eye; lawn/façade need a look).
- **Topology rule learned the hard way**: the camera MUST sit behind the hub (hub
  re-times the signal at the end of the extension run). Extension-direct = firmware
  lockup within minutes. Powered hub replacing the unpowered one = fixes both signal
  and power; order one.
- Aimer gotcha: aim.py auto-stops after 30 min (default) — pass a bigger limit for
  long aiming sessions; preview flicker is auto-exposure + gamma shimmer, cosmetic.
