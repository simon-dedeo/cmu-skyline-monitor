#!/bin/bash
# capture.sh — one capture tick of the CMU Courtyard Monitor.
#
# Invoked by launchd (com.lsm.monitor) every 300 s (5 min). Each run:
#   1. runs update.py  -> refreshes courtyard.jpg, sky.jpg, data.json
#   2. archives the two frames under archive/YYYY-MM-DD/HHMM_*.jpg  (EVERY tick, 5 min)
#      (+ an archive-only night-boost variant when dark; see 2b)
#   3. uploads to santafe SPARSELY: live weather (data.json/js) ~every 15 min, and
#      the sky PHOTO only when goldpeak.py finds a new peak-golden frame in a
#      golden-hour window. Courtyard frame and gallery are no longer uploaded.
#
# Deliberately simple: single-frame captures, no HDR / exposure sweep.
# Daytime frames from these Arducams look great straight out of imagesnap;
# the old HDR path produced muddy, reflection-ghosted night frames.

set -u
cd "$(dirname "$0")" || exit 1
export PATH="/opt/local/bin:/opt/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin"

PY=/opt/local/bin/python3.11
LOG="./monitor.log"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
DAY="$(date -u +%Y-%m-%d)"
HHMM="$(date -u +%H%M)"

# --- Upload destination (proven working: reused from the old run.sh) ---
UPLOAD_ENABLED=true
SSH_KEY="$HOME/.ssh/id_rsa"        # the key authorized on santafe (ed25519 is NOT)
DEST_HOST="simon@santafe.santafe.edu"
DEST_DIR="/home/simon/html"           # served at https://sites.santafe.edu/~simon/

log(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >>"$LOG"; }

# Serialize camera access so we never collide with the timelapse/experiment jobs.
source "$(dirname "$0")/camlock.sh"
camlock_acquire "$LOG" || exit 0

log "tick $STAMP start"

# During a golden-hour window the 2.5-min goldscan job owns the sky camera; this
# tick reuses its latest frame instead of capturing again (see update.py), so the
# camera sees 2 multi-exposure captures per 5 min, not 6 (Simon 2026-07-14 — the
# dawn duty cycle wedged the GS cam firmware). Window check uses the previous
# tick's data.json; the solar times don't move between ticks.
GOLD_W="$("$PY" goldpeak.py --window data.json 2>/dev/null || echo NONE)"
FRESH_MAX=120
if [ "$GOLD_W" = "morning" ] || [ "$GOLD_W" = "evening" ]; then
  export GOLD_SKY_REUSE=1
  FRESH_MAX=300      # goldscan refreshes sky.jpg every ~150 s
fi

# 1. Capture + refresh data
if "$PY" update.py >>"$LOG" 2>&1; then
  log "update.py ok"
else
  log "update.py FAILED (rc=$?)"
fi

# 2. Archive both frames EVERY tick (5-min disk cadence — this is what feeds the
#    timelapse movie; santafe uploads are deliberately sparse, see step 3).
#    Only archive a FRESHLY captured frame: if a cam is unplugged (the courtyard
#    cam currently is), update.py leaves its old .jpg in place — skip that stale
#    frame so we don't archive hundreds of identical duplicates.
mkdir -p "archive/$DAY"
NOW_S="$(date +%s)"
# sky_std = the standardized single metered frame (saved by expose.py alongside the
# adaptive sky.jpg) — archived too so we can review single-vs-adaptive over the week.
for cam in courtyard sky sky_std; do
  if [ -s "$cam.jpg" ]; then
    MT="$(stat -f %m "$cam.jpg" 2>/dev/null || echo 0)"
    if [ "$((NOW_S - MT))" -le "$FRESH_MAX" ]; then
      cp "$cam.jpg" "archive/$DAY/${HHMM}_${cam}.jpg"
    fi
  fi
done
log "archived -> archive/$DAY/${HHMM}_*.jpg (fresh frames only)"

# 2a. Camera-wedge watchdog (2026-07-14 incident: GS cam firmware locked up, kiosk
#     froze for hours). If the sky frame is still stale after this tick's capture,
#     count consecutive failures; at 3 (~15 min dead) run the recovery sequence
#     that clears a wedged camera daemon: kill VDCAssistant -> USB device reset ->
#     kill again (the reset only opens once no client holds the device). We hold
#     the camlock here, so no capture can be in flight. A firmware lockup that
#     survives this needs a physical replug.
SKY_AGE=$(( NOW_S - $(stat -f %m sky.jpg 2>/dev/null || echo 0) ))
if [ "$SKY_AGE" -gt 240 ]; then
  FAILS=$(( $(cat .skyfail 2>/dev/null || echo 0) + 1 ))
  echo "$FAILS" > .skyfail
  if [ "$FAILS" -ge 3 ]; then
    if "$PY" usbreset.py --check >>"$LOG" 2>&1; then
      log "camera watchdog: sky ${SKY_AGE}s stale after $FAILS ticks -> reset sequence"
      sudo -n killall VDCAssistant 2>/dev/null
      sleep 2
      "$PY" usbreset.py >>"$LOG" 2>&1
      sudo -n killall VDCAssistant 2>/dev/null
    else
      # Camera not on the bus at all (unplugged?) — a reset can't help; don't
      # thrash the daemon every 15 min. Physical replug required.
      log "camera watchdog: sky ${SKY_AGE}s stale but GS cam ABSENT on USB — skipping reset"
    fi
    echo 0 > .skyfail
  fi
else
  echo 0 > .skyfail
fi

# 2b. Night-boost pilot (ARCHIVE-ONLY, never uploaded). When the sky frame is
#     dark (twilight/night), also grab a gain-boosted variant to pull out the
#     blue-hour gradient / city glow the flicker-free exposure leaves black, so
#     we can build a nicer dusk/dawn movie. Deep night stays mostly noise — we
#     just won't use those frames.
#     Skipped inside golden windows — goldscan already archives a frame every
#     2.5 min there, and we're keeping the camera duty cycle down.
if [ -s "sky.jpg" ] && [ -z "${GOLD_SKY_REUSE:-}" ]; then
  SKYMEAN="$("$PY" -c 'import cv2; f=cv2.imread("sky.jpg"); print(int(f.mean()) if f is not None else 255)' 2>/dev/null || echo 255)"
  if [ "${SKYMEAN:-255}" -lt 60 ]; then
    if "$PY" expose.py sky_night "archive/$DAY/${HHMM}_sky_night.jpg" >>"$LOG" 2>&1; then
      log "night-boost variant saved (sky mean=$SKYMEAN)"
    else
      log "night-boost variant FAILED"
    fi
  fi
fi

# 2c-pre. Fixed-settings REFERENCE frame (Simon 2026-07-14): single grab at a
#     deterministic sun-elevation-keyed exposure (see expose.py sky_ref) — the
#     colour-of-the-day bars read THIS frame so metering/HDR can't move them.
#     Archived to its own tree (~250 KB × 288/day ≈ 72 MB/day) for back-analysis.
#     Light on the camera (one stream session, no bracket) and inside the camlock.
if REF_META="$("$PY" expose.py sky_ref sky_ref.jpg 2>>"$LOG")"; then
  printf '%s' "$REF_META" > .sky_ref_meta.json
  mkdir -p "archive_ref/$DAY"
  cp sky_ref.jpg "archive_ref/$DAY/${HHMM}.jpg"
else
  log "sky_ref capture FAILED (colour bars fall back to the metered frame)"
fi

# 2c. Tier-0 science log: append per-frame metrics to science.csv (kept LOCAL) and
#     refresh skycolor.js (today's 5-min average-sky-colour samples for the dashboard
#     colour-of-the-day bar; uploaded with the weather files below).
"$PY" science.py >>"$LOG" 2>&1 || log "science.py error"

# --- Live weather upload to santafe, throttled to ~15 min (840s fires every 3rd
#     5-min tick). Kept sparse (Simon: "don't upload too much"). The sky PHOTO is
#     NOT uploaded here — the peak-golden frame is handled separately by the
#     2.5-min goldscan job (com.lsm.goldscan) during golden-hour windows.
if [ "$UPLOAD_ENABLED" = true ]; then
  # -C compresses (text files shrink ~3x); one scp of all files = ONE ssh connection.
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
      log "uploaded weather+skycolor ($FILES ) in one connection"
    else
      log "weather upload FAILED"
    fi
    echo "$NOW_EPOCH" > "$UPLOAD_STATE"
  else
    log "weather upload skipped (last was $((NOW_EPOCH - LAST_UP))s ago, < ${UPLOAD_MIN_INTERVAL}s)"
  fi
fi

log "tick $STAMP done"

# Keep the log from growing without bound (last ~2000 lines)
tail -n 2000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
