#!/bin/bash
set -u; cd ~/refiner || exit 1; LOG=~/refiner/refiner.log
P=proofsandreasons@pandr.wifi.local.cmu.edu
say(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [refiner] $*" >>"$LOG"; }
say "start"
rsync -a --delete -e "ssh -o BatchMode=yes -o ConnectTimeout=20" "$P:monitor/flatfield_acc/" ~/refiner/frames/ >>"$LOG" 2>&1 && say "frames synced ($(ls ~/refiner/frames/*.png 2>/dev/null | wc -l))" || say "frame sync FAILED"
scp -q -o BatchMode=yes "$P:monitor/spot_lab/spot_photometry.csv" ~/refiner/photometry.csv 2>/dev/null; scp -q -o BatchMode=yes "$P:monitor/drift.csv" ~/refiner/drift.csv >>"$LOG" 2>&1 || say "drift sync FAILED"
OUT=$(python3 ~/refiner/refiner.py 2>>"$LOG"); say "$OUT"
if false; then  # push DISABLED 2026-07-28: spotnight/publish_live.py owns the pandr contract now
  scp -q -o BatchMode=yes ~/refiner/spot_tmap.npy "$P:monitor/spot_tmap.npy" >>"$LOG" 2>&1; scp -q -o BatchMode=yes ~/refiner/spot_model.json "$P:monitor/spot_model.json" >>"$LOG" 2>&1 && say "spot_model.json pushed to pandr" || say "push FAILED"
fi
say "done"
