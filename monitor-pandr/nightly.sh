#!/bin/bash
# nightly.sh -- nightly A/B verification, retention pruning, ganesha backup, and log hygiene
# (launchd com.lsm.spotnightly). The Bayesian Stan M2 fit + TMAP + tracking now run on
# akdeniz (spotnight.py) and publish directly to pandr.
set -u
cd "$HOME/monitor" || exit 0
LOG="spot_lab/nightly.log"
log(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [nightly] $*" >>"$LOG"; }
mkdir -p spot_lab
log "start"

# 1. Matched A/B verification of the live corrector -> spot_lab/ab_log.csv
~/monitor/venv/bin/python3 spot_ab.py >>"$LOG" 2>&1 && log "spot_ab done" || log "spot_ab FAILED"

# 2. Blemish-edge alarm (reads recent locked provenance)
~/monitor/venv/bin/python3 - >>"$LOG" 2>&1 <<'EDGEPY'
import csv, datetime, statistics as st
rows = [r for r in csv.DictReader(open("spot_provenance.csv")) if r.get("cx") and r.get("cy")]
cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=36)
cy = []
for r in rows[-2000:]:
    try:
        if datetime.datetime.fromisoformat(r["ts"]) >= cut:
            cy.append(float(r["cy"]))
    except Exception:
        pass
if len(cy) < 10:
    print("[edge] only %d recent rows -- cannot assess blemish edge margin" % len(cy))
else:
    m = st.median(cy)
    tier = ("CRITICAL: lock cannot measure upward" if m < 60 else
            "WARN: lock upward range reduced"      if m < 85 else
            "WARN: background annulus clipped"     if m < 105 else "ok")
    print("[edge] blemish median cy = %.1f px from frame top (n=%d) -- %s" % (m, len(cy), tier))
    if tier != "ok":
        print("[edge] ACTION: move the camera UP a few mm")
EDGEPY

# 3. Local bracket retention (protect last 48h for spot_ab)
~/monitor/venv/bin/python3 spot_prune.py --root "$HOME/monitor/spot_lab/brackets" \
    --protect-hours 48 --apply >>"$LOG" 2>&1 && log "local bracket prune done" \
    || log "local bracket prune FAILED (non-fatal)"

# 4. Ganesha backup (free-space floor 30 GB)
T0=$(date +%s)
ssh_gan(){ ssh -i "$HOME/.ssh/id_ed25519" -o BatchMode=yes -o ConnectTimeout=25 \
                 -o StrictHostKeyChecking=accept-new simon@ganesha.lan.cmu.edu "$@"; }
RS="rsync -a --partial -e 'ssh -i $HOME/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new'"
DEST="simon@ganesha.lan.cmu.edu:/mnt/anopotamia/spot_lab_pandr/"
FLOOR_GB=30
GAN_AVAIL_GB=$(ssh_gan 'df -P -k /mnt/anopotamia' 2>/dev/null | awk 'NR==2{printf "%d", $4/1048576}')
if [ -z "$GAN_AVAIL_GB" ]; then
  log "backup SKIPPED: cannot read ganesha free space"
elif [ "$GAN_AVAIL_GB" -lt "$FLOOR_GB" ]; then
  log "backup SKIPPED -- ALARM: ganesha:/mnt/anopotamia has only ${GAN_AVAIL_GB} GB free (floor ${FLOOR_GB} GB)"
elif eval $RS spot_lab drift.csv science.csv "$DEST" >>"$LOG" 2>&1; then
  log "backup rsync OK ($(( $(date +%s) - T0 ))s, ganesha had ${GAN_AVAIL_GB} GB free)"
else
  log "backup rsync FAILED (will retry tomorrow; local rings unaffected)"
fi

if ssh_gan 'test -d /mnt/anopotamia/spot_lab_pandr/spot_lab/brackets' 2>/dev/null; then
  ssh_gan 'python3 /home/simon/spot_prune.py --root /mnt/anopotamia/spot_lab_pandr/spot_lab/brackets --apply' \
      >>"$LOG" 2>&1 && log "ganesha bracket prune done" || log "ganesha bracket prune FAILED (non-fatal)"
fi

# 5. Wi-Fi channel observatory rebuild
python3 apledger.py >>"$LOG" 2>&1 && log "apledger rebuilt" || log "apledger FAILED (non-fatal)"

# 6. Log rotation hygiene (keep web.log bounded)
if [ -f web.log ] && [ "$(stat -f %z web.log 2>/dev/null || echo 0)" -gt 5242880 ]; then
  tail -n 10000 web.log > web.log.tmp 2>/dev/null && mv web.log.tmp web.log
fi

log "done"
tail -n 3000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
