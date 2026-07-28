#!/bin/bash
# nightly.sh — LEGACY nightly analysis (launchd com.lsm.spotnightly; fires ~06:40 local
# in practice). Since 2026-07-28 the LIVE correction model is produced by spotnight.py +
# publish_live.py on akdeniz; this job only maintains the M1/M2 photometric record and
# the per-pixel research maps, plus the ganesha backup.
# Runs ENTIRELY on the Air (no supervision for a week is expected):
#   1. spotfit.R   — Bayesian M2 fit per channel (Stan; rlm fallback) + M1 falsification
#   2. spotmap.py  — per-pixel (non-circular) spot map from the crop stack
#   3. rsync backup of spot_lab + flatfield_acc + key CSVs to ganesha:/mnt/anopotamia
#      (1.7 TB free; internal CMU; --partial resumes; backlog logged as a health metric)
# Everything fail-soft; the log is spot_lab/nightly.log.
set -u
cd "$HOME/monitor" || exit 0
LOG="spot_lab/nightly.log"
log(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [nightly] $*" >>"$LOG"; }
mkdir -p spot_lab
log "start"

/opt/homebrew/bin/Rscript spotfit.R >>"$LOG" 2>&1 && log "spotfit done" || log "spotfit FAILED"
~/monitor/venv/bin/python3 spotmap.py >>"$LOG" 2>&1 && log "spotmap done" || log "spotmap FAILED"

T0=$(date +%s)
RS="rsync -a --partial -e 'ssh -i $HOME/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new'"
DEST="simon@ganesha.lan.cmu.edu:/mnt/anopotamia/spot_lab_pandr/"
if eval $RS spot_lab drift.csv science.csv "$DEST" >>"$LOG" 2>&1 \
   && eval $RS flatfield_acc "$DEST" >>"$LOG" 2>&1; then
  log "backup rsync OK ($(( $(date +%s) - T0 ))s)"
else
  log "backup rsync FAILED (will retry tomorrow; local rings unaffected)"
fi
log "done"
tail -n 3000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
