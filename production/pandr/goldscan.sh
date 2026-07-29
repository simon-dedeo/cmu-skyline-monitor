#!/bin/bash
# goldscan.sh — one 2.5-minute golden-hour scan tick. Invoked directly by goldtick.sh
# (under the com.lsm.goldscan GUI LaunchAgent, so SkyCam.app can reach the camera via
# `open`). During golden windows this job OWNS the sky camera:
# it captures + scores a frame, refreshes the live sky.jpg for the kiosk (the 5-min
# tick reuses it — see GOLD_SKY_REUSE in capture.sh/update.py), and archives EVERY
# scan as HHMM_gold.jpg (Simon 2026-07-14: save all the rapid ticks). NOTHING is
# uploaded here — goldtick.sh uploads the final peak just ONCE, when the window
# closes, to keep santafe transfer minimal. Shares the camlock.
set -u
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin:/usr/sbin:/sbin"
PY=/Users/proofsandreasons/monitor/venv/bin/python3
LOG="./monitor.log"
DAY="$(date -u +%Y-%m-%d)"; HHMM="$(date -u +%H%M)"
log(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [goldscan] $*" >>"$LOG"; }

source "$(dirname "$0")/camlock.sh"
camlock_acquire "$LOG" || exit 0     # busy (5-min tick mid-capture) -> skip this minute

mkdir -p goldscan "archive/$DAY"
SCAN="goldscan/${HHMM}_sky.jpg"
# Capture the golden frame as an HDR fusion (skyshot.sh hdr: 3-exposure bracket, fixed
# daylight WB so the gold stays gold instead of being neutralised by auto-WB). Runs in
# the GUI session (goldtick invokes us directly under com.lsm.goldscan) and holds the
# camlock we took above. Golden hour is the highest-dynamic-range scene (bright sky +
# dark foreground), so HDR matters most here.
if bash skyshot.sh hdr "$SCAN" >>"$LOG" 2>&1 && [ -s "$SCAN" ]; then
  cp -f "${SCAN}.meta" .last_gold_meta.json 2>/dev/null || printf '{"mode":"hdr","device":"Elgato Facecam 4K"}' > .last_gold_meta.json
else
  log "sky capture failed (SkyCam/HDR)"; exit 0
fi

# Refresh the live kiosk frame (atomic rename so the web server never sees a
# partial file) and keep every golden-window scan for review.
cp "$SCAN" sky.jpg.gold.tmp && mv sky.jpg.gold.tmp sky.jpg
cp "$SCAN" "archive/$DAY/${HHMM}_gold.jpg"

# goldpeak.py updates .golden_peak.json (the running peak) and prints UPLOAD on a new
# peak. We only need to stash the frame; the upload happens at window close.
GP="$("$PY" goldpeak.py "$SCAN" data.json 2>>"$LOG")"
if [ "${GP%%|*}" = "UPLOAD" ]; then
  IFS='|' read -r _ G_WHICH _ _ _ _ _ _ _ G_BLEND _ <<EOF
$GP
EOF
  cp "$SCAN" .goldpeak_frame.jpg
  cp "$SCAN" "archive/$DAY/peak_${G_WHICH}.jpg"     # local record of this window's best
  # publish IMMEDIATELY (Simon 2026-07-28): the peak goes to the front page and the
  # index as soon as it is taken; window close re-publishes the final state (idempotent)
  bash gold_publish.sh || log "live publish helper failed"
  log "new peak ($G_WHICH, blend $G_BLEND) saved + published live"
fi
