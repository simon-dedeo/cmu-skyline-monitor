#!/bin/bash
# gold_publish.sh — publish the CURRENT golden-hour peak to the front page and archives
# NOW (Simon 2026-07-28: peaks go live as soon as they are taken, not at window close).
# Reads .golden_peak.json + .goldpeak_frame.jpg, builds golden.js, upserts the index,
# and ships sky.jpg + dated peak + golden.js + goldindex.js to santafe and ganesha in
# one connection each. Idempotent: goldtick.sh calls it again at window close as the
# finalizer. Fail-soft: a failed upload never breaks the scan tick.
set -u
cd "$(dirname "$0")" || exit 1
PY=/Users/proofsandreasons/monitor/venv/bin/python3
LOG="./monitor.log"
SSH_KEY="$HOME/.ssh/id_ed25519"
DEST_HOST="simon@santafe.santafe.edu"
DEST_DIR="${GOLD_DEST_DIR:-/home/simon/html}"
log(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [goldpub] $*" >>"$LOG"; }

[ -s .goldpeak_frame.jpg ] || exit 0
GIDX="$("$PY" - <<'PY'
import json
try:
    s = json.load(open(".golden_peak.json"))
except Exception:
    s = {}
if s.get("which"):
    g = {k: s.get(k) for k in ("which", "time", "date", "ts", "sun_elev",
                               "sky_hue", "sky_hue_name", "img_hue", "img_hue_name",
                               "campus_hue", "campus_hue_name", "exp_ms", "gain",
                               "gold_score", "red_pct", "amber_pct")}
    g["blend"] = round(s.get("blend", 0))
    open("golden.js", "w").write("window.GOLDEN=" + json.dumps(g) + ";")
    import sys; sys.path.insert(0, "."); from goldpeak import RULE_ID
    print("|".join(str(s.get(k, "")) for k in
                   ("date", "which", "time", "sun_elev", "sky_hue", "sky_hue_name",
                    "gold_score", "red_pct")) + "|" + RULE_ID)
PY
)"
[ -n "$GIDX" ] || exit 0
IFS='|' read -r F_DATE F_WHICH F_TIME F_SE F_SH F_SN F_GS F_RP F_RULE <<EOF2
$GIDX
EOF2
"$PY" goldindex.py "$F_DATE" "$F_WHICH" "$F_TIME" "$F_SE" "$F_SH" "$F_SN" "$F_GS" "$F_RP" "$F_RULE" >>"$LOG" 2>&1
SCP="scp -q -C -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new"
[ -f "$SSH_KEY" ] && SCP="$SCP -i $SSH_KEY"
TMP="$(mktemp -d)"
cp .goldpeak_frame.jpg "$TMP/sky.jpg"
cp .goldpeak_frame.jpg "$TMP/peak_${F_DATE}_${F_WHICH}.jpg"
cp golden.js goldindex.js "$TMP/"
# 1600-px thumbnail for hours.html (it used to load the 4K file as its thumbnail)
"$PY" - "$TMP/peak_${F_DATE}_${F_WHICH}.jpg" "$TMP/thumb_${F_DATE}_${F_WHICH}.jpg" <<'PYT' || log "thumb generation failed"
import sys, cv2
img = cv2.imread(sys.argv[1]); h, w = img.shape[:2]
if w > 1600: img = cv2.resize(img, (1600, int(round(h * 1600 / w))), interpolation=cv2.INTER_AREA)
cv2.imwrite(sys.argv[2], img, [cv2.IMWRITE_JPEG_QUALITY, 88])
PYT
if $SCP "$TMP"/* "$DEST_HOST:$DEST_DIR/"; then
  log "peak published live ($F_WHICH $F_TIME, elev $F_SE) -> santafe"
else
  log "santafe live publish FAILED"
fi
cp .goldpeak_frame.jpg "$TMP/golden.jpg"
GANC="scp -q -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -i $HOME/.ssh/id_ed25519"
if $GANC "$TMP/golden.jpg" "$TMP/peak_${F_DATE}_${F_WHICH}.jpg" "$TMP/golden.js" "$TMP/goldindex.js" "simon@ganesha.lan.cmu.edu:/data/www/"; then
  log "peak published live -> ganesha"
else
  log "ganesha live publish FAILED"
fi
rm -rf "$TMP"
exit 0
