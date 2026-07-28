#!/bin/bash
# ladder.sh — dedicated calibration ladders (SPOT.md §2.5). Called at the END of a capture
# tick by capture.sh, which still HOLDS the camlock — so a ladder can never collide with a
# capture by construction. Each slot fires once per day (guard files .ladder_<slot>_<date>):
#   exp1420 / exp1720 / exp2020 : 8-point ×2 exposure ladder at UTC 14:20 / 17:20 / 20:20
#   exptwi   : same exposure ladder during evening civil twilight (sun −5..−1°)
#   gaindusk : gain ladder {0,40,80,120,160} at the plan exposure (sun −8..−4°)
#   gainnight: gain ladder at exposure 1000 (sun < −15°)
# Every rung: set UVC, capture lossless PNG (warmup flushes ≥ the required settle frames),
# verify the level actually stepped, log photometry via spotlab.py. Fail-soft throughout.
set -u
cd "$(dirname "$0")" || exit 0
PY=/Users/proofsandreasons/monitor/venv/bin/python3
UVC="$HOME/monitor/uvc-util -I 0"; APP="$HOME/monitor/SkyCam.app"; DEV="Elgato Facecam 4K"
LOG=./monitor.log
log(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [ladder] $*" >>"$LOG"; }
u(){ $UVC -s "$1" >/dev/null 2>&1; }
restore(){ u auto-exposure-mode=8; u auto-white-balance-temp=true; }
trap restore EXIT INT TERM

DATE=$(date -u +%Y%m%d); HM=$(date -u +%H%M)
ELEV=$($PY -c 'import solar,datetime;print(round(solar.sun_elevation(datetime.datetime.now(datetime.timezone.utc)),2))' 2>/dev/null || echo 99)
EXPREF=$($PY -c "import skyexp;print(skyexp.plan($ELEV)['exposure'])" 2>/dev/null || echo 12)

SLOT=""
if   [ "$HM" -ge 1420 ] && [ "$HM" -le 1428 ]; then SLOT=exp1420
elif [ "$HM" -ge 1720 ] && [ "$HM" -le 1728 ]; then SLOT=exp1720
elif [ "$HM" -ge 2020 ] && [ "$HM" -le 2028 ]; then SLOT=exp2020
elif $PY -c "import sys;sys.exit(0 if -5<=$ELEV<=-1 else 1)" 2>/dev/null; then SLOT=exptwi
elif $PY -c "import sys;sys.exit(0 if -8<=$ELEV<-5 else 1)" 2>/dev/null; then SLOT=gaindusk
elif $PY -c "import sys;sys.exit(0 if $ELEV<-15 else 1)" 2>/dev/null; then SLOT=gainnight
fi
[ -n "$SLOT" ] || exit 0
GUARD=".ladder_${SLOT}_${DATE}"
[ -f "$GUARD" ] && exit 0
touch "$GUARD"
rm -f .ladder_*_$(date -u -v-2d +%Y%m%d 2>/dev/null) 2>/dev/null   # drop stale guards

DIR="spot_lab/ladders/$DATE"; mkdir -p "$DIR"
FREE=$(df -g / | awk 'NR==2{print $4}')
shot(){ open -W -a "$APP" --args --device "$DEV" --out "$(pwd)/$1" --warmup 1.0 --png >/dev/null 2>&1 && [ -s "$1" ]; }

u auto-white-balance-temp=false; u white-balance-temp=5600; u auto-exposure-mode=1
PREV="x"
rung(){ # rung FILE ROLE EXP GAIN
  local F="$1" ROLE="$2" E="$3" G="$4"
  if shot "$F"; then
    local M; M=$($PY -c "import cv2;print(round(float(cv2.imread('$F',0).mean()),1))" 2>/dev/null || echo "?")
    [ "$M" = "$PREV" ] && log "$SLOT $ROLE level did not step (mean=$M)"
    PREV="$M"
    $PY spotlab.py log "$F" "$ROLE" "$E" "$G" >>"$LOG" 2>&1
    rm -f "$F.meta"
    [ "${FREE:-0}" -lt 60 ] && rm -f "$F"
  fi
}
case "$SLOT" in
  gain*)
    E=$EXPREF; [ "$SLOT" = gainnight ] && E=1000
    u exposure-time-abs=$E
    for G in 0 40 80 120 160; do
      u gain=$G
      rung "$DIR/${HM}_${SLOT}_E${E}_G${G}.png" "ladder_${SLOT}_G${G}" "$E" "$G"
    done
    u gain=0
    ;;
  *)
    u gain=0
    TOP=$((EXPREF * 4)); [ "$TOP" -gt 1000 ] && TOP=1000
    E=$TOP
    for i in 1 2 3 4 5 6 7 8; do
      [ "$E" -lt 1 ] && break
      u exposure-time-abs=$E
      rung "$DIR/${HM}_${SLOT}_E${E}.png" "ladder_${SLOT}_E${E}" "$E" "0"
      E=$((E / 2))
    done
    ;;
esac
log "$SLOT ladder complete (free=${FREE}G)"
