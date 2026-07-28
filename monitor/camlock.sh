#!/bin/bash
# camlock.sh — serialize USB-camera access across the monitor jobs (dashboard,
# timelapse, experiment). Concurrent access corrupts frames (e.g. one job sets a
# manual exposure while another captures -> blown-white courtyard). mkdir is
# atomic, so it makes a clean mutex. Stale locks (holder died/hung) are stolen
# after 240 s. Usage:  source camlock.sh; camlock_acquire "$LOG" || exit 0
CAMLOCK="$HOME/monitor/.camlock"
camlock_acquire() {
  local logf="${1:-/dev/null}" i age
  for i in $(seq 1 150); do            # up to ~300 s
    if mkdir "$CAMLOCK" 2>/dev/null; then
      echo "$$" > "$CAMLOCK/owner" 2>/dev/null
      trap 'rm -rf "$CAMLOCK" 2>/dev/null' EXIT INT TERM
      return 0
    fi
    if [ -d "$CAMLOCK" ]; then
      age=$(( $(date +%s) - $(stat -f %m "$CAMLOCK" 2>/dev/null || echo 0) ))
      [ "$age" -gt 240 ] && rm -rf "$CAMLOCK" 2>/dev/null
    fi
    sleep 2
  done
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) camlock: busy >300s, skipping this run" >> "$logf"
  return 1
}
