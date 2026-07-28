#!/bin/bash
# capture.sh — one capture tick of the CMU Courtyard Monitor.
#
# Invoked by launchd (com.lsm.monitor) every 300 s (5 min). Each run:
#   0. captures sky.jpg from the Elgato Facecam 4K via SkyCam.app (see below)
#   1. runs update.py  -> refreshes data.json from that frame + weather/solar
#   2. archives the sky frame under archive/YYYY-MM-DD/HHMM_sky.jpg  (EVERY tick, 5 min)
#   3. uploads to santafe SPARSELY: live weather (data.json/js) ~every 15 min, and
#      the sky PHOTO only when goldpeak.py finds a new peak-golden frame in a
#      golden-hour window. Courtyard frame and gallery are no longer uploaded.
#
# Deliberately simple: one 4K auto-exposed still per tick from the Elgato Facecam
# (via SkyCam.app). No HDR / exposure sweep — the old multi-exposure path produced
# muddy, reflection-ghosted night frames.

set -u
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin:/usr/sbin:/sbin"

PY=/Users/proofsandreasons/monitor/venv/bin/python3
LOG="./monitor.log"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
DAY="$(date -u +%Y-%m-%d)"
HHMM="$(date -u +%H%M)"

# --- Upload destination (proven working: reused from the old run.sh) ---
UPLOAD_ENABLED=true
SSH_KEY="$HOME/.ssh/id_ed25519"        # the key authorized on santafe
DEST_HOST="simon@santafe.santafe.edu"
DEST_DIR="/home/simon/html"           # served at https://sites.santafe.edu/~simon/

log(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >>"$LOG"; }

# Serialize camera access so we never collide with the timelapse/experiment jobs.
source "$(dirname "$0")/camlock.sh"
camlock_acquire "$LOG" || exit 0

log "tick $STAMP start"

# --- Capture from the Elgato Facecam 4K via skyshot.sh (SkyCam.app + uvc-util exposure
#     control). Two products per tick:
#       sky.jpg     = HDR-fused DISPLAY frame (3-exposure bracket, fixed daylight WB) —
#                     the pretty image on the kiosk + photos.proofsandreasons.io.
#       sky_ref.jpg = FIXED-exposure radiometric REFERENCE (sun-elevation-keyed exposure
#                     + gain) — the consistent frame science.py + the timelapse read.
#     During a golden-hour window the 2.5-min goldscan job OWNS the display frame, so
#     reuse its sky.jpg here and only refresh our own fixed reference (keeps the science
#     series continuous through twilight). update.py never opens the camera — it just
#     reads whichever sky.jpg is current. Window check uses last tick's data.json (solar
#     times don't move between ticks). ---
GOLD_W="$("$PY" goldpeak.py --window data.json 2>/dev/null || echo NONE)"
FRESH_MAX=120
if [ "$GOLD_W" = "morning" ] || [ "$GOLD_W" = "evening" ]; then
  FRESH_MAX=300      # goldscan refreshes sky.jpg every ~150 s; only take our own reference
  if bash skyshot.sh ref sky_ref.jpg >>"$LOG" 2>&1; then
    log "sky_ref (fixed) captured [golden window: display frame reused from goldscan]"
  else log "sky_ref capture FAILED"; fi
else
  if bash skyshot.sh hdr sky.jpg >>"$LOG" 2>&1 && [ -s sky.jpg ]; then
    log "sky.jpg HDR display frame captured"
  else log "sky.jpg HDR capture FAILED"; fi
  if bash skyshot.sh ref sky_ref.jpg >>"$LOG" 2>&1; then
    log "sky_ref (fixed) captured"
  else log "sky_ref capture FAILED"; fi
fi
export GOLD_SKY_REUSE=1

# 1. Capture + refresh data
if "$PY" update.py >>"$LOG" 2>&1; then
  log "update.py ok"
else
  log "update.py FAILED (rc=$?)"
fi

# 2. Archive the sky frame EVERY tick (5-min disk cadence — this feeds the timelapse
#    movie; santafe uploads are deliberately sparse, see step 3). Only archive a
#    FRESHLY captured frame: if SkyCam failed this tick, update.py leaves the old
#    sky.jpg in place — skip it so we don't archive identical duplicates.
mkdir -p "archive/$DAY"
NOW_S="$(date +%s)"
for cam in sky; do
  if [ -s "$cam.jpg" ]; then
    MT="$(stat -f %m "$cam.jpg" 2>/dev/null || echo 0)"
    if [ "$((NOW_S - MT))" -le "$FRESH_MAX" ]; then
      cp "$cam.jpg" "archive/$DAY/${HHMM}_${cam}.jpg"
    fi
  fi
done
log "archived -> archive/$DAY/${HHMM}_sky.jpg (fresh frames only)"

# Fixed-exposure REFERENCE series -> its own tree. Constant exposure per sun elevation
# makes this the smoothest source for a timelapse AND the radiometric record for science
# back-analysis (brightness changes = real sky changes, not metering drift).
if [ -s sky_ref.jpg ]; then
  RMT="$(stat -f %m sky_ref.jpg 2>/dev/null || echo 0)"
  if [ "$((NOW_S - RMT))" -le 360 ]; then
    mkdir -p "archive_ref/$DAY"; cp sky_ref.jpg "archive_ref/$DAY/${HHMM}.jpg"
    log "archived reference -> archive_ref/$DAY/${HHMM}.jpg"
  fi
fi

# 2a. Sky-frame watchdog. SkyCam captures fresh each tick via `open -W` and exits, so
#     there's no persistent client to wedge — a stall is rare. If the frame is still
#     stale after this tick, count consecutive failures; at 3 (~15 min) bounce the
#     macOS camera daemon (VDCAssistant), a cheap reset the next `open -W` recovers
#     from. A stall surviving this needs a physical replug of the Elgato.
SKY_AGE=$(( NOW_S - $(stat -f %m sky.jpg 2>/dev/null || echo 0) ))
if [ "$SKY_AGE" -gt 240 ]; then
  FAILS=$(( $(cat .skyfail 2>/dev/null || echo 0) + 1 ))
  echo "$FAILS" > .skyfail
  if [ "$FAILS" -ge 3 ]; then
    sudo -n killall VDCAssistant 2>/dev/null && log "camera watchdog: sky ${SKY_AGE}s stale x$FAILS ticks -> bounced VDCAssistant"
    echo 0 > .skyfail
  fi
else
  echo 0 > .skyfail
fi

# 2b/2c-pre. The fixed-exposure REFERENCE frame is captured above (skyshot.sh ref, via
#     uvc-util manual exposure) and archived to archive_ref/ just above. The old separate
#     night-boost variant is subsumed by the HDR bracket — its long-exposure member pulls
#     out the blue-hour gradient / city glow — so there's no separate dark grab here.

# 2c. Tier-0 science log: append per-frame metrics to science.csv (kept LOCAL) and
#     refresh skycolor.js (today's 5-min average-sky-colour samples for the dashboard
#     colour-of-the-day bar; uploaded with the weather files below).
"$PY" science.py >>"$LOG" 2>&1 || log "science.py error"

# 2d. Aim-drift tick: phase-correlate this tick's fixed-exposure reference against the
#     pinned baseline (building band) -> one row in drift.csv. Shows mount settling/creep
#     over days; also lets the spot model separate pixel-fixed blemishes from scene motion.
DRIFT_OUT="$("$PY" drift.py --tick 2>>"$LOG")" && log "aim $DRIFT_OUT"
case "$DRIFT_OUT" in
  *drift\ *) DX="${DRIFT_OUT#drift }"; DX="${DX%%,*}"; DX="${DX#+}"; DX="${DX%%.*}"; DX="${DX#-}"
             [ "${DX:-0}" -gt 40 ] 2>/dev/null && log "aim-drift WARNING: |dx| > 40 px — check mount / re-aim + re-pin baseline";;
esac
# The lossless reference PNG was the processing input (science read it above). Instead of
# discarding it, roll it into the flat-field accumulation window so the spot model refines
# as conditions vary (clouds, brightness). Rolling cap keeps disk bounded (~576 ≈ 2 days);
# rebuild the gain map periodically with `flatfield.py build`.
if [ -s sky_ref.png ]; then
  # SPOT.md §2.1: per-tick photometry on the fixed-exposure reference frame (spot + controls)
  REFMETA="$("$PY" -c 'import json;m=json.load(open("sky_ref.jpg.meta"));print(m.get("exposure",0),m.get("gain",0))' 2>/dev/null || echo "0 0")"
  "$PY" spotlab.py log sky_ref.png ref $REFMETA >>"$LOG" 2>&1
  mkdir -p flatfield_acc
  mv -f sky_ref.png "flatfield_acc/$(date -u +%Y%m%d_%H%M%S).png"
  # 14-day reference ring (SPOT.md §2.4; 288/day x 14 = 4032 frames)
  ls -1t flatfield_acc/*.png 2>/dev/null | tail -n +4033 | while read -r f; do rm -f "$f"; done
else
  rm -f sky_ref.png
fi

# Weekly: rebuild the flat-field spot map from the accumulated window so it tracks changing
# conditions (and any new / cleared blemishes). Guarded to run ~once a week, not per tick.
GAINAGE=$(( NOW_S - $(stat -f %m .spot_gain.npy 2>/dev/null || echo "$NOW_S") ))
NACC=$(ls flatfield_acc/*.png 2>/dev/null | wc -l | tr -d ' ')
if [ "$GAINAGE" -gt 604800 ] && [ "$NACC" -ge 50 ]; then
  "$PY" flatfield.py build >>"$LOG" 2>&1 && log "flat-field spot map rebuilt from ~$NACC frames"
fi

# --- Live upload to ganesha (INTERNAL CMU — free/fast) EVERY tick (~5 min): the 4K HDR
#     DISPLAY frame AND the small dashboard DATA (data.js/data.json/skycolor.js). ganesha
#     is local to CMU, so — unlike santafe — there's no reason to throttle: the public
#     dashboard at proofsandreasons.io reads all of these from photos.proofsandreasons.io
#     and updates every tick. rsync writes temp+rename, so a reader never sees a
#     half-written file. This is NOT bulk storage — the full archive stays local on pandr;
#     ganesha holds only "the current live frame + data".
if [ "$UPLOAD_ENABLED" = true ] && [ -s sky.jpg ]; then
  GANFILES="sky.jpg"
  for f in data.js data.json skycolor.js; do [ -f "$f" ] && GANFILES="$GANFILES $f"; done
  if rsync -q -e "ssh -i $HOME/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new" \
       $GANFILES "simon@ganesha.lan.cmu.edu:/data/www/"; then
    log "uploaded live sky.jpg + dashboard data to ganesha:/data/www"
  else
    log "ganesha upload FAILED"
  fi
fi

# --- Small weather/colour DATA to santafe, throttled ~15 min (BACKUP now). The public
#     dashboard reads these from ganesha every tick (above); this santafe copy is kept as
#     a fallback and for any santafe-hosted page. Golden-hour PEAK images go to santafe
#     via goldtick.sh. No large per-tick images go to santafe.
if [ "$UPLOAD_ENABLED" = true ]; then
  SCP="scp -q -C -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new"
  [ -f "$SSH_KEY" ] && SCP="$SCP -i $SSH_KEY"
  UPLOAD_MIN_INTERVAL=840
  UPLOAD_STATE="./.last_upload"
  NOW_EPOCH="$(date -u +%s)"
  LAST_UP="$(cat "$UPLOAD_STATE" 2>/dev/null || echo 0)"
  if [ "$((NOW_EPOCH - LAST_UP))" -ge "$UPLOAD_MIN_INTERVAL" ]; then
    FILES=""
    for f in data.json data.js skycolor.js; do [ -f "$f" ] && FILES="$FILES $f"; done
    if [ -n "$FILES" ] && $SCP $FILES "$DEST_HOST:$DEST_DIR/"; then
      log "uploaded weather+skycolor ($FILES ) to santafe"
    else
      log "santafe data upload FAILED"
    fi
    echo "$NOW_EPOCH" > "$UPLOAD_STATE"
  else
    log "santafe data upload skipped (last was $((NOW_EPOCH - LAST_UP))s ago, < ${UPLOAD_MIN_INTERVAL}s)"
  fi
fi

# 2f. spot_lab retention (SPOT.md §2.2/§2.4/§2.5) — once per day: crops 21 d, bracket ring
#     14 d, ladder full-frames 14 d. Nightly df assertion lives in the archiving steps
#     themselves (they skip archiving below 60 GB free; capture is never skipped).
if [ ! -f ".spotgc_$DAY" ]; then
  rm -f .spotgc_* 2>/dev/null
  find spot_lab/crops -type f -mtime +21 -delete 2>/dev/null
  find spot_lab/brackets -type f -mtime +14 -delete 2>/dev/null
  find spot_lab/ladders -type f -mtime +14 -delete 2>/dev/null
  find spot_lab -type d -empty -delete 2>/dev/null
  touch ".spotgc_$DAY"
fi

# 2g. Calibration ladders (SPOT.md §2.5): fires only in its daily slots (solar-noon-ish UTC
#     windows, twilight, dusk/night gain); runs under THIS tick's camlock, so it can never
#     collide with a capture. Fail-soft — never delays or breaks the tick.
bash ladder.sh || true

log "tick $STAMP done"

# Keep the log from growing without bound (last ~2000 lines)
tail -n 2000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
