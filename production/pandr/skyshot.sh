#!/bin/bash
# skyshot.sh MODE OUTFILE — capture a sky frame from the Elgato Facecam 4K via SkyCam.app,
# with UVC exposure control through uvc-util. Called by capture.sh / goldscan.sh.
#
#   auto  OUT        camera auto-exposure + auto-WB (the live "as the eye sees it" look).
#   ref   OUT.jpg    FIXED radiometric reference: manual, sun-elevation-keyed exposure+gain
#                    (skyexp.py), fixed daylight WB. Captured LOSSLESS (PNG) for processing,
#                    then saved as OUT.jpg (q95); the sibling .png is left for science.py to
#                    read this tick (capture.sh deletes it afterward). Writes OUT + OUT.meta.
#   hdr   OUT.jpg    exposure bracket captured LOSSLESS (PNG) -> Mertens fusion + tone pass
#                    (hdrfuse.py) -> one DISPLAY JPEG. Fixed WB so golden stays golden.
#                    Process on uncompressed, save JPEG. Falls back to one frame if fusion fails.
#
# The camera is ALWAYS returned to auto mode on exit (trap), so the live tick + aimer are
# never left stuck in manual. Assumes the caller already holds the camlock.
set -u
cd "$(dirname "$0")" || exit 1
MODE="${1:-auto}"; OUT="${2:-}"
[ -n "$OUT" ] || { echo "usage: skyshot.sh auto|ref|hdr OUTFILE" >&2; exit 64; }

PY=/Users/proofsandreasons/monitor/venv/bin/python3
UVC="$HOME/monitor/uvc-util -I 0"
APP="$HOME/monitor/SkyCam.app"
DEV="Elgato Facecam 4K"
WB_FIXED=5600                     # daylight WB (K) for reference + HDR (keeps golden warm)

u(){ $UVC -s "$1" >/dev/null 2>&1; }
restore_auto(){ u auto-exposure-mode=8; u auto-white-balance-temp=true; }
trap restore_auto EXIT INT TERM

# One SkyCam still, written ATOMICALLY to $1 (capture to a temp, then rename) so a live
# reader never sees a partial frame. $2 = warmup seconds (short: in manual mode the
# exposure is already fixed, so the sensor only needs a frame or two to flush).
shot(){
  local dst="$1" tmp="$1.part.jpg"
  open -W -a "$APP" --args --device "$DEV" --out "$(pwd)/$tmp" --warmup "${2:-0.8}" >/dev/null 2>&1
  rm -f "$tmp.meta"                       # discard SkyCam's raw dims sidecar
  if [ -s "$tmp" ]; then mv -f "$tmp" "$dst"; return 0; fi
  rm -f "$tmp"; return 1
}

# Same, but LOSSLESS PNG (uncompressed sensor buffer) — the PROCESSING input for HDR fusion
# and the science reference, where JPEG blocking would be amplified by CLAHE / metrics.
shotpng(){
  local dst="$1" tmp="$1.part.png"
  open -W -a "$APP" --args --device "$DEV" --out "$(pwd)/$tmp" --warmup "${2:-0.8}" --png >/dev/null 2>&1
  rm -f "$tmp.meta"
  if [ -s "$tmp" ]; then mv -f "$tmp" "$dst"; return 0; fi
  rm -f "$tmp"; return 1
}

if [ "$MODE" = "auto" ]; then
  restore_auto
  shot "$OUT" 2.0 && [ -s "$OUT" ]; exit $?
fi

# ref / hdr both need the sun elevation -> exposure plan.
ELEV="$($PY -c 'import solar,datetime;print(round(solar.sun_elevation(datetime.datetime.now(datetime.timezone.utc)),2))' 2>/dev/null || echo 0)"
eval "$($PY skyexp.py --sh "$ELEV" 2>/dev/null || echo 'EXPOSURE=12 GAIN=0 BRACKET="6 12 24"')"

# Fixed radiometric setup shared by ref + hdr.
u auto-white-balance-temp=false
u white-balance-temp=$WB_FIXED
u auto-exposure-mode=1            # manual

if [ "$MODE" = "ref" ]; then
  u gain=$GAIN
  u exposure-time-abs=$EXPOSURE
  RAW="${OUT%.jpg}.png"                   # lossless frame science.py reads; capture.sh rm's it
  if shotpng "$RAW" 1.0 && [ -s "$RAW" ]; then
    "$PY" -c "import cv2,sys,flatfield;cv2.imwrite(sys.argv[2],flatfield.apply(cv2.imread(sys.argv[1])),[cv2.IMWRITE_JPEG_QUALITY,95])" "$RAW" "$OUT"
    printf '{"mode":"ref","exposure":%s,"gain":%s,"wb":%s,"sun_elev":%s}' \
           "$EXPOSURE" "$GAIN" "$WB_FIXED" "$ELEV" > "${OUT}.meta"
    exit 0
  fi
  exit 1
fi

if [ "$MODE" = "hdr" ]; then
  u gain=$GAIN
  # SINGLE camera session: all bracket exposures captured back-to-back (~1.5 s span vs ~11 s when
  # relaunching SkyCam per exposure) -> far less cloud drift between bracket frames. skycap
  # re-asserts MANUAL exposure per shot (AVFoundation re-enables auto on a live session).
  TMPS=""; n=0; BRKARGS=""
  for E in $BRACKET; do
    f="${OUT}.br${n}.png"; BRKARGS="$BRKARGS --brk ${E},$(pwd)/${f}"; TMPS="$TMPS $f"; n=$((n+1))
  done
  # shellcheck disable=SC2086
  open -W -a "$APP" --args --device "$DEV" --png --warmup 0.8 --settle 0.4 --uvc "$HOME/monitor/uvc-util" $BRKARGS >/dev/null 2>&1
  KEEP=""; for f in $TMPS; do rm -f "$f.meta"; [ -s "$f" ] && KEEP="$KEEP $f"; done; TMPS="$KEEP"

  # SPOT.md §2.1/§2.4: after fusion, photometry-log EVERY bracket frame (spot + controls)
  # and archive it into the 14-day ring for detection stacks — instead of deleting.
  # df-guarded: below 60 GB free we log but do not archive.
  harvest_brackets(){
    local STAMP2 FREE hn=0 hE hf
    STAMP2="$(date -u +%Y%m%d_%H%M%S)"
    FREE="$(df -g / | awk 'NR==2{print $4}')"
    mkdir -p spot_lab/brackets
    for hE in $BRACKET; do
      hf="${OUT}.br${hn}.png"
      if [ -s "$hf" ]; then
        "$PY" spotlab.py log "$hf" "br${hn}" "$hE" "$GAIN"
        if [ "${FREE:-0}" -gt 60 ]; then
          mv -f "$hf" "spot_lab/brackets/${STAMP2}_br${hn}_E${hE}.png"
        else
          rm -f "$hf"
        fi
      fi
      hn=$((hn+1))
    done
    rm -f "${OUT}".br*.png "${OUT}".br*.png.part.png
  }

  # shellcheck disable=SC2086
  if $PY hdrfuse.py "${OUT}.part.jpg" $TMPS && [ -s "${OUT}.part.jpg" ]; then
    mv -f "${OUT}.part.jpg" "$OUT"        # atomic publish of the fused display JPEG
    printf '{"mode":"hdr","bracket":"%s","gain":%s,"wb":%s,"sun_elev":%s}' \
           "$BRACKET" "$GAIN" "$WB_FIXED" "$ELEV" > "${OUT}.meta"
    harvest_brackets
    exit 0
  fi
  # fusion failed -> harvest what we have, fall back to a single JPEG frame
  harvest_brackets
  rm -f "${OUT}.part.jpg"
  restore_auto
  shot "$OUT" 2.0 && [ -s "$OUT" ]; exit $?
fi

echo "unknown mode: $MODE" >&2; exit 64
