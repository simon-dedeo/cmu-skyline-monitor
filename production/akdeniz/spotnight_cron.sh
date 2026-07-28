#!/bin/bash
# spotnight_cron.sh -- nightly window-spot estimation on akdeniz.
#
# Runs at 13:00 local, which is after (a) pandr's nightly rsync of spot_lab + drift.csv to
# the ganesha mirror, which lands ~10:30, and (b) the 10:00 daily refiner. Pulls fresh
# frames from the mirror rather than from pandr directly: ganesha->akdeniz is wired and
# fast, pandr is on wifi and is the live capture host.
#
# Fail-soft throughout. A failed pull continues with whatever frames are already local,
# because a stale-but-good parameter set beats no run at all. Success is asserted from
# the job's own artifacts, never from an exit code -- a long command over the reverse
# tunnel returns 255 when the transport drops even though the work completed.
set -u
STATE=/home/simon/spot_state
LOG="$STATE/cron.log"
mkdir -p "$STATE"
{
  echo "=== $(date -u +%FT%TZ) spotnight cron start"
  MIRROR=simon@ganesha.lan.cmu.edu:/mnt/anopotamia/spot_lab_pandr
  if rsync -a --delete -e "ssh -o BatchMode=yes -o ConnectTimeout=30" \
        "$MIRROR/spot_lab/brackets/" /home/simon/brackets/; then
    echo "brackets synced: $(ls /home/simon/brackets | wc -l) files"
  else
    echo "WARN bracket rsync failed; continuing with $(ls /home/simon/brackets | wc -l) local files"
  fi
  rsync -a -e "ssh -o BatchMode=yes -o ConnectTimeout=30" \
        "$MIRROR/drift.csv" /home/simon/drift.csv \
    && echo "drift.csv synced" || echo "WARN drift.csv rsync failed; using existing"

  SPOT_BRACKETS=/home/simon/brackets \
  SPOT_DRIFT=/home/simon/drift.csv \
  SPOT_STATE_DIR="$STATE" \
  python3 -u /home/simon/spotnight.py

  # publish the tracked position + registered transmittance map into the live
  # corrector contract on pandr (replaces the old refiner push, disabled 2026-07-28)
  python3 /home/simon/publish_live.py && echo "publish_live OK" || echo "publish_live FAILED (live keeps last good)"

  if [ -f "$STATE/last_run_ok" ]; then
    echo "artifact check OK: marker $(cat "$STATE/last_run_ok")"
  else
    echo "ERROR no completion marker written"
  fi
  echo "=== $(date -u +%FT%TZ) spotnight cron done"
} >> "$LOG" 2>&1
# keep the log bounded
tail -n 5000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
