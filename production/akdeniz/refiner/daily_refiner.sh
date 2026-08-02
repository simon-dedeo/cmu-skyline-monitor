#!/bin/bash
set -u; cd ~/refiner || exit 1; LOG=~/refiner/refiner.log
P=proofsandreasons@pandr.wifi.local.cmu.edu
M=simon@ganesha.lan.cmu.edu:/mnt/anopotamia/spot_lab_pandr
RSH="ssh -o BatchMode=yes -o ConnectTimeout=20"
say(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [refiner] $*" >>"$LOG"; }
say "start"
# Frames: pandr first, then the ganesha mirror (pandr rsyncs flatfield_acc there nightly,
# so the mirror lags up to ~1 day -- fine for a daily refit, and far better than refitting
# frames that have stopped advancing at all). Before 2026-08-02 the direct pull was the
# ONLY path and it fails soft, so when akdeniz lost its route to pandr on 07-29 these
# frames silently froze for five days while the refiner "succeeded" on stale input.
# NOTE: pandr's rsync is openrsync (2.6.9-compatible) -- no --info/--partial-dir there.
if rsync -a --delete -e "$RSH" "$P:monitor/flatfield_acc/" ~/refiner/frames/ >>"$LOG" 2>&1; then
  say "frames synced from pandr ($(ls ~/refiner/frames/*.png 2>/dev/null | wc -l))"
elif rsync -a -e "$RSH" "$M/flatfield_acc/" ~/refiner/frames/ >>"$LOG" 2>&1; then
  say "WARN pandr unreachable; frames synced from ganesha mirror, up to ~1d behind ($(ls ~/refiner/frames/*.png 2>/dev/null | wc -l))"
else
  say "ALARM frame sync FAILED from BOTH pandr and the ganesha mirror; refitting STALE frames"
fi
scp -q -o BatchMode=yes "$P:monitor/spot_lab/spot_photometry.csv" ~/refiner/photometry.csv 2>/dev/null \
  || rsync -a -e "$RSH" "$M/spot_lab/spot_photometry.csv" ~/refiner/photometry.csv >>"$LOG" 2>&1 \
  || say "WARN photometry sync failed; scurve refits on existing rows"
scp -q -o BatchMode=yes "$P:monitor/drift.csv" ~/refiner/drift.csv 2>/dev/null \
  || rsync -a -e "$RSH" "$M/drift.csv" ~/refiner/drift.csv >>"$LOG" 2>&1 \
  || say "WARN drift sync failed; using existing"
OUT=$(python3 ~/refiner/refiner.py 2>>"$LOG"); say "$OUT"
if false; then  # push DISABLED 2026-07-28: spotnight/publish_live.py owns the pandr contract now
  scp -q -o BatchMode=yes ~/refiner/spot_tmap.npy "$P:monitor/spot_tmap.npy" >>"$LOG" 2>&1; scp -q -o BatchMode=yes ~/refiner/spot_model.json "$P:monitor/spot_model.json" >>"$LOG" 2>&1 && say "spot_model.json pushed to pandr" || say "push FAILED"
fi
say "done"
