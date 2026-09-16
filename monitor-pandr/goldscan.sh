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
if [ -f .gold_exact ]; then
  # exact-position tick: the crossing happens once -- retry the lock briefly rather
  # than skipping the minute
  OK=""
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if camlock_acquire "$LOG"; then OK=1; break; fi
    sleep 3
  done
  rm -f .gold_exact
  [ -n "$OK" ] || exit 0
else
  camlock_acquire "$LOG" || exit 0   # busy (5-min tick mid-capture) -> skip this minute
fi

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
  # publish whenever the running peak is INSIDE the pick band (Simon 2026-08-18). The
  # pick is now dynamic within PICK_RANGE and scored on sky redness, so any in-band peak
  # is a legitimate display frame; a later, redder in-band frame simply republishes.
  # (Was: publish only within 0.3 deg of a single TARGET_ELEV.)
  ATPOS="$("$PY" - <<'PYCHK'
import json, sys
sys.path.insert(0, ".")
from goldpeak import PICK_RANGE, COLOUR_MIN, GREY_TARGET
try:
    s = json.load(open(".golden_peak.json"))
    e = float(s["sun_elev"]); gs = float(s.get("gold_score") or 0.0)
    gt = GREY_TARGET.get(s.get("which"), 0.0)
    inband = PICK_RANGE[0] <= e <= PICK_RANGE[1]
    # Publish live for a COLOURED peak immediately (rare, and the whole point). On a grey
    # day the running peak improves on almost every frame of a 6-deg-wide band, which would
    # re-ship a 4K JPEG ~10x per window; santafe transfer is deliberately sparse, so grey
    # peaks only go live near the grey target (-2 deg; the old 0.3-deg
    # behaviour) and the window-close finalizer in goldtick.sh publishes whatever best exists.
    print("OK" if inband and (gs >= COLOUR_MIN or abs(e - gt) <= 0.3) else "NO")
except Exception:
    print("NO")
PYCHK
)"
  if [ "$ATPOS" = "OK" ]; then
    bash gold_publish.sh || log "live publish helper failed"
    log "new peak ($G_WHICH, blend $G_BLEND) IN BAND -> published live"
  else
    log "new peak ($G_WHICH, blend $G_BLEND) saved (grey/out-of-band; finalizer publishes)"
  fi
fi
