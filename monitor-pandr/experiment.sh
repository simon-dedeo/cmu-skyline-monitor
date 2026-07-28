#!/bin/bash
# experiment.sh — one weekend A/B run. launchd (com.lsm.experiment) invokes this
# every 30 min via the ssh->localhost hop (so it inherits camera TCC access).
set -u
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin:/usr/sbin:/sbin"

# Serialize camera access with the dashboard/timelapse jobs (experiments yield).
source "$(dirname "$0")/camlock.sh"
camlock_acquire "./experiment.log" || exit 0

/Users/proofsandreasons/monitor/venv/bin/python3 experiment.py >> ./experiment.log 2>&1
tail -n 800 experiment.log > experiment.log.tmp 2>/dev/null && mv experiment.log.tmp experiment.log
