#!/bin/bash
# timelapse.sh — hourly full-res frames from BOTH cameras for long-term timelapses.
#
#   sky       : controlled exposure (flicker-free) via expose.py
#   courtyard : auto exposure via expose.py (that model can't be driven manually)
#
# Frames are archived under timelapse/<cam>/YYYY-MM-DD_HH00Z.jpg (chronologically
# sortable for ffmpeg) and the latest of each is uploaded for a live web view.
# Invoked hourly by launchd (com.lsm.timelapse) via ssh->localhost, so this runs
# under sshd and inherits the camera (TCC) grant -> expose.py can reach the cams.

set -u
cd "$(dirname "$0")" || exit 1
export PATH="/opt/local/bin:/opt/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin"

PY=/opt/local/bin/python3.11
BASE="$HOME/monitor/timelapse"
LOG="$HOME/monitor/timelapse.log"
STAMP="$(date -u +%Y-%m-%d_%H00)Z"
SSH_KEY="$HOME/.ssh/id_rsa"                        # the key santafe authorizes
DEST_HOST="simon@santafe.santafe.edu"
DEST_DIR="/home/simon/html"                        # https://sites.santafe.edu/~simon/

log(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >>"$LOG"; }

# Serialize camera access with the dashboard/experiment jobs.
source "$(dirname "$0")/camlock.sh"
camlock_acquire "$LOG" || exit 0

log "tick $STAMP start"

for cam in sky courtyard; do
  dir="$BASE/$cam"; mkdir -p "$dir"
  frame="$dir/$STAMP.jpg"
  meta="$("$PY" expose.py "$cam" "$frame" 2>/dev/null)"
  if [ -s "$frame" ]; then
    log "$cam: $STAMP.jpg $(stat -f %z "$frame")B  $meta"
    if scp -q -o BatchMode=yes -o ConnectTimeout=20 -i "$SSH_KEY" "$frame" "$DEST_HOST:$DEST_DIR/${cam}_hourly.jpg"; then
      log "$cam: uploaded -> ${cam}_hourly.jpg"
    else
      log "$cam: upload FAILED"
    fi
  else
    log "$cam: capture FAILED"; rm -f "$frame"
  fi
done

# (The browsable gallery is published by the 15-min job — capture.sh — for a
# denser view; this hourly job just archives full-res frames for the long-term
# timelapse compile.)

log "tick $STAMP done"
tail -n 3000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"

# Compile when you have a span of frames (either camera):
#   ffmpeg -framerate 24 -pattern_type glob -i '~/monitor/timelapse/sky/*.jpg' \
#       -c:v libx264 -pix_fmt yuv420p -crf 18 sky_timelapse.mp4
