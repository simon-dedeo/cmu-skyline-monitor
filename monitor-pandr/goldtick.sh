#!/bin/bash
# goldtick.sh — 2.5-minute golden-hour scanner entry point (launchd: com.lsm.goldscan).
#
# During a golden-hour window it hops to goldscan.sh (capture + score, saving the
# running peak LOCALLY). When the window CLOSES it uploads the final peak to santafe
# exactly ONCE — sky.jpg + a dated peak_<date>_<which>.jpg + golden.js + goldindex.js
# — so the whole day's golden traffic is ~2 images, not one-per-minute. Outside a
# window (and after finalizing) it is a cheap no-op that also cleans up scan frames.
set -u
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin:/usr/sbin:/sbin"
PY=/Users/proofsandreasons/monitor/venv/bin/python3
LOG="./monitor.log"
SSH_KEY="$HOME/.ssh/id_ed25519"
DEST_HOST="simon@santafe.santafe.edu"
DEST_DIR="${GOLD_DEST_DIR:-/home/simon/html}"     # override for testing
log(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [goldtick] $*" >>"$LOG"; }

W="$("$PY" goldpeak.py --window data.json 2>/dev/null)"
if [ "$W" = "morning" ] || [ "$W" = "evening" ]; then
  echo "$W" > .gold_window                        # mark the window active
  # Run the scan directly: SkyCam.app gets the camera grant via `open` (goldscan.sh),
  # and this LaunchAgent already runs in the GUI session, so no ssh->localhost hop is
  # needed (that was the old-camera Big-Sur trick, which doesn't grant camera on
  # Sequoia anyway). goldscan.sh takes the camlock itself.
  exec bash "$(dirname "$0")/goldscan.sh"
fi

# --- Outside a golden window ---
if [ -f .gold_window ] && [ -s .goldpeak_frame.jpg ]; then
  # A window just closed and we have a peak -> build golden.js + index, upload ONCE.
  GIDX="$("$PY" - <<'PY'
import json
try:
    s = json.load(open(".golden_peak.json"))
except Exception:
    s = {}
if s.get("which"):
    g = {k: s.get(k) for k in ("which", "time", "date", "ts", "sun_elev",
                               "sky_hue", "sky_hue_name", "img_hue", "img_hue_name",
                               "campus_hue", "campus_hue_name", "exp_ms", "gain")}
    g["blend"] = round(s.get("blend", 0))
    open("golden.js", "w").write("window.GOLDEN=" + json.dumps(g) + ";")
    print("|".join(str(s.get(k, "")) for k in
                   ("date", "which", "time", "sun_elev", "sky_hue", "sky_hue_name")))
PY
)"
  if [ -n "$GIDX" ]; then
    IFS='|' read -r F_DATE F_WHICH F_TIME F_SE F_SH F_SN <<EOF
$GIDX
EOF
    "$PY" goldindex.py "$F_DATE" "$F_WHICH" "$F_TIME" "$F_SE" "$F_SH" "$F_SN" >>"$LOG" 2>&1
    # Stage all four with their final names and send in ONE scp connection (-C).
    SCP="scp -q -C -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new"
    [ -f "$SSH_KEY" ] && SCP="$SCP -i $SSH_KEY"
    TMP="$(mktemp -d)"
    cp .goldpeak_frame.jpg "$TMP/sky.jpg"
    cp .goldpeak_frame.jpg "$TMP/peak_${F_DATE}_${F_WHICH}.jpg"
    cp golden.js goldindex.js "$TMP/"
    if $SCP "$TMP"/* "$DEST_HOST:$DEST_DIR/"; then
      log "window $F_WHICH closed -> uploaded final peak ($F_TIME) + index to santafe"
    else
      log "golden finalize upload FAILED"
    fi
    # Also hold the golden hour on ganesha (photos.proofsandreasons.io). The peak
    # is named golden.jpg there — ganesha's sky.jpg is the LIVE 5-min frame, so a
    # distinct name avoids clobbering it. Small + internal (CMU); this is what the
    # main page's golden panel now reads.
    cp .goldpeak_frame.jpg "$TMP/golden.jpg"
    GANC="scp -q -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -i $HOME/.ssh/id_ed25519"
    if $GANC "$TMP/golden.jpg" "$TMP/peak_${F_DATE}_${F_WHICH}.jpg" "$TMP/golden.js" "$TMP/goldindex.js" "simon@ganesha.lan.cmu.edu:/data/www/"; then
      log "golden also uploaded to ganesha (golden.jpg + peak + index)"
    else
      log "ganesha golden upload FAILED"
    fi
    rm -rf "$TMP"
  fi
fi
rm -f .gold_window .goldpeak_frame.jpg goldscan/*.jpg 2>/dev/null
exit 0
