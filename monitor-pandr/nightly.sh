#!/bin/bash
# nightly.sh — LEGACY nightly analysis (launchd com.lsm.spotnightly; fires ~06:40 local
# in practice). Since 2026-07-28 the LIVE correction model is produced by spotnight.py +
# publish_live.py on akdeniz; this job only maintains the M1/M2 photometric record and
# the per-pixel research maps, plus the ganesha backup.
# Runs ENTIRELY on the Air (no supervision for a week is expected):
#   1. spotfit.R   — Bayesian M2 fit per channel (Stan; rlm fallback) + M1 falsification
#   2. spotmap.py  — per-pixel (non-circular) spot map from the crop stack
#   3. spot_ab.py   — matched A/B (corrector on vs off) -> spot_lab/ab_log.csv
#   3c/4b. spot_prune.py — bracket retention (golden hour + 15-min cadence), both sides
#   4. rsync backup of spot_lab + flatfield_acc + key CSVs to ganesha:/mnt/anopotamia
#      (SHARED 1.8 TB disk, filled to 0 on 2026-08-31; internal CMU; --partial resumes;
#      gated behind a free-space floor -- see 4a; backlog logged as a health metric)
# Everything fail-soft; the log is spot_lab/nightly.log.
set -u
cd "$HOME/monitor" || exit 0
LOG="spot_lab/nightly.log"
log(){ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [nightly] $*" >>"$LOG"; }
mkdir -p spot_lab
log "start"

/opt/homebrew/bin/Rscript spotfit.R >>"$LOG" 2>&1 && log "spotfit done" || log "spotfit FAILED"
~/monitor/venv/bin/python3 spotmap.py >>"$LOG" 2>&1 && log "spotmap done" || log "spotmap FAILED"

# 4. spot_ab.py — matched A/B of the LIVE corrector (added 2026-08-18). The original A/B
#    (archive_precorrect vs archive_corrected) stopped 2026-07-27, leaving 3 weeks with no
#    check that the correction still worked. Writes spot_lab/ab_log.csv, which the rsync below
#    already carries to ganesha. Sandboxed: it cannot touch spot_provenance.csv.
~/monitor/venv/bin/python3 spot_ab.py >>"$LOG" 2>&1 && log "spot_ab done" || log "spot_ab FAILED"

# 3b. blemish-EDGE alarm (added 2026-08-19). The blemish migrates toward the frame TOP and
#     three things break as cy falls, silently: the r=105-130 background annulus is clipped
#     (cy<105), the template-lock search loses upward range (cy<85), and below cy~60 it cannot
#     measure upward AT ALL (the search window clamps at 0) -- so it would report lock=1 with a
#     one-sided offset while the blemish keeps moving. Nothing was watching for this. Reads the
#     provenance that is already written every tick; does not touch the live corrector.
~/monitor/venv/bin/python3 - >>"$LOG" 2>&1 <<'EDGEPY'
import csv, datetime, statistics as st
rows = [r for r in csv.DictReader(open("spot_provenance.csv")) if r.get("lock") == "1"]
cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=36)
cy = []
for r in rows:
    try:
        if datetime.datetime.fromisoformat(r["ts"]) >= cut: cy.append(float(r["cy"]))
    except Exception:
        pass
if len(cy) < 10:
    print("[edge] only %d locked rows in 36h -- cannot assess blemish edge margin" % len(cy))
else:
    m = st.median(cy)
    tier = ("CRITICAL: lock cannot measure upward" if m < 60 else
            "WARN: lock upward range reduced"      if m < 85 else
            "WARN: background annulus clipped"     if m < 105 else "ok")
    print("[edge] blemish median cy = %.1f px from frame top (n=%d) -- %s" % (m, len(cy), tier))
    if tier != "ok":
        print("[edge] ACTION: move the camera UP; see SPOT.md (annulus can no longer be seated)")
EDGEPY

# 3c. BRACKET RETENTION, local side (added 2026-08-31). Keep golden hour + a 15-min
#     cadence; --protect-hours 48 shields what spot_ab.py still needs (it reads 26 h).
#     Runs BEFORE the backup so the rsync never ships frames we are about to drop.
~/monitor/venv/bin/python3 spot_prune.py --root "$HOME/monitor/spot_lab/brackets" \
    --protect-hours 48 --apply >>"$LOG" 2>&1 && log "local bracket prune done" \
    || log "local bracket prune FAILED (non-fatal)"

T0=$(date +%s)
ssh_gan(){ ssh -i "$HOME/.ssh/id_ed25519" -o BatchMode=yes -o ConnectTimeout=25 \
                 -o StrictHostKeyChecking=accept-new simon@ganesha.lan.cmu.edu "$@"; }
RS="rsync -a --partial -e 'ssh -i $HOME/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new'"
DEST="simon@ganesha.lan.cmu.edu:/mnt/anopotamia/spot_lab_pandr/"

# 4a. FREE-SPACE FLOOR (added 2026-08-31). anopotamia is SHARED (reddit-archive, HANSARD,
#     user-archives) and this backup grows without bound -- rsync -a, no --delete, ~3 GB/day
#     of bracket PNGs. On 2026-08-31 it ran the disk to exactly 0 bytes, which did NOT just
#     break the backup: /data/www (the public webroot) lived on the same filesystem, so
#     photos.proofsandreasons.io froze and the dashboard camera stopped updating for 5.5 h
#     while every tick logged "ganesha upload FAILED" unread. The webroot has since been
#     moved off this disk to /data/www on sda2, but a full disk is still a hard stop for the
#     backup and for every other tenant -- so refuse to start unless there is real headroom.
#     Skipping a backup is cheap (local rings hold the data); filling a shared disk is not.
FLOOR_GB=30
#     df -P -k is the portable form (no --output), KB -> GB in awk.
GAN_AVAIL_GB=$(ssh_gan 'df -P -k /mnt/anopotamia' 2>/dev/null | awk 'NR==2{printf "%d", $4/1048576}')
if [ -z "$GAN_AVAIL_GB" ]; then
  log "backup SKIPPED: cannot read ganesha free space (host down or ssh refused)"
elif [ "$GAN_AVAIL_GB" -lt "$FLOOR_GB" ]; then
  log "backup SKIPPED -- ALARM: ganesha:/mnt/anopotamia has only ${GAN_AVAIL_GB} GB free (floor ${FLOOR_GB} GB)"
  log "backup ACTION: prune spot_lab_pandr/spot_lab/brackets (oldest PNGs, ~10 MB each) or move the backup to /data2"
elif eval $RS spot_lab drift.csv science.csv "$DEST" >>"$LOG" 2>&1 \
   && eval $RS flatfield_acc "$DEST" >>"$LOG" 2>&1; then
  log "backup rsync OK ($(( $(date +%s) - T0 ))s, ganesha had ${GAN_AVAIL_GB} GB free)"
else
  log "backup rsync FAILED (will retry tomorrow; local rings unaffected)"
fi

# 4b. BRACKET RETENTION, archive side (added 2026-08-31). The rsync has no --delete and
#     pandr's ring buffer drops brackets at 14 days, so anything that reached ganesha
#     inside the 48 h local shield would otherwise live there forever -- that unbounded
#     drift is exactly what filled the disk. Idempotent (each 15-min bin keeps its
#     earliest tick), so a nightly run is a no-op once the tail is thinned.
if ssh_gan 'test -d /mnt/anopotamia/spot_lab_pandr/spot_lab/brackets' 2>/dev/null; then
  ssh_gan 'python3 /home/simon/spot_prune.py --root /mnt/anopotamia/spot_lab_pandr/spot_lab/brackets --apply' \
      >>"$LOG" 2>&1 && log "ganesha bracket prune done" || log "ganesha bracket prune FAILED (non-fatal)"
fi
log "done"
tail -n 3000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
