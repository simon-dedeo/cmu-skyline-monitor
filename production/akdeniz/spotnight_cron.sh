#!/bin/bash
# spotnight_cron.sh -- nightly window-blemish tracking, Bayesian M2 fit, and live publish (akdeniz).
#
# Runs 13:00 local, after pandr's ~07:00 rsync of spot_lab to the ganesha mirror.
# Pulls BOTH brackets and spot_photometry.csv over wired LAN from ganesha (zero heavy
# Wi-Fi traffic on pandr -- no 50 GB flatfield_acc rsync and no door-RSSI disturbance).
# Fail-soft throughout; success is asserted from the completion marker.
set -u
STATE=/home/simon/spot_state
LOG="$STATE/cron.log"
M=simon@ganesha.lan.cmu.edu:/mnt/anopotamia/spot_lab_pandr
RSH="ssh -o BatchMode=yes -o ConnectTimeout=30"
mkdir -p "$STATE" /home/simon/brackets /home/simon/refiner
{
  echo "=== $(date -u +%FT%TZ) spotnight cron start"
  if rsync -a --delete -e "$RSH" "$M/spot_lab/brackets/" /home/simon/brackets/; then
    echo "brackets synced: $(ls /home/simon/brackets | wc -l) files"
  else
    echo "WARN bracket rsync failed; continuing with $(ls /home/simon/brackets | wc -l) local files"
  fi
  if rsync -a -e "$RSH" "$M/spot_lab/spot_photometry.csv" /home/simon/refiner/photometry.csv; then
    echo "photometry synced from ganesha"
  else
    echo "WARN photometry sync failed; fitting on existing local copy"
  fi

  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    SPOT_BRACKETS=/home/simon/brackets SPOT_PHOTOMETRY=/home/simon/refiner/photometry.csv \
    SPOT_STATE_DIR="$STATE" python3 -u /home/simon/spotnight.py

  if [ -f "$STATE/last_run_ok" ]; then
    echo "artifact check OK: marker $(cat "$STATE/last_run_ok")"
  else
    echo "ERROR no completion marker written"
  fi
  echo "=== $(date -u +%FT%TZ) spotnight cron done"
} >> "$LOG" 2>&1
tail -n 5000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
