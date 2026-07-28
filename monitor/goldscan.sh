#!/bin/bash
# goldscan.sh — one 2.5-minute golden-hour scan tick. Invoked by goldtick.sh via
# ssh->localhost (camera TCC). During golden windows this job OWNS the sky camera:
# it captures + scores a frame, refreshes the live sky.jpg for the kiosk (the 5-min
# tick reuses it — see GOLD_SKY_REUSE in capture.sh/update.py), and archives EVERY
# scan as HHMM_gold.jpg (Simon 2026-07-14: save all the rapid ticks). NOTHING is
# uploaded here — goldtick.sh uploads the final peak just ONCE, when the window
# closes, to keep santafe transfer minimal. Shares the camlock.
set -u
cd "$(dirname "$0")" || exit 1
export PATH="/opt/local/bin:/opt/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin"
PY=/opt/local/bin/python3.11
LOG="./monitor.log"
DAY="$(date -u +%Y-%m-%d)"; HHMM="$(date -u +%H%M)"
log(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [goldscan] $*" >>"$LOG"; }

source "$(dirname "$0")/camlock.sh"
camlock_acquire "$LOG" || exit 0     # busy (5-min tick mid-capture) -> skip this minute

mkdir -p goldscan "archive/$DAY"
SCAN="goldscan/${HHMM}_sky.jpg"
# stdout = the capture meta JSON (kept for the tick to reuse); stderr = meter log.
if ! META="$("$PY" expose.py sky "$SCAN" 2>>"$LOG")"; then
  log "sky capture failed"; exit 0
fi
[ -n "$META" ] && printf '%s' "$META" > .last_gold_meta.json

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
  log "new peak ($G_WHICH, blend $G_BLEND) saved locally — upload at window close"
fi
