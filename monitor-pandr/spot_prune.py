#!/usr/bin/env python3
"""spot_prune.py — bounded retention for the bracket stack (added 2026-08-31).

WHY THIS EXISTS. The nightly backup is `rsync -a` with no --delete, so the ganesha copy
grew without bound (~3 GB/day of 4K bracket PNGs) while pandr's own ring buffer dropped
them after 14 days. On 2026-08-31 it filled ganesha's shared 1.8 TB anopotamia disk to
exactly 0 bytes. That did not merely break the backup: /data/www — the public webroot —
sat on the same filesystem, so photos.proofsandreasons.io froze and the dashboard camera
stopped updating for 5.5 h while every tick logged "ganesha upload FAILED" unread.

RETENTION RULE (Simon, 2026-08-31): keep golden-hour selections, and otherwise a
15-minute cadence. Concretely a TICK (one YYYYMMDD_HHMMSS bracket group) survives if

  * GOLDEN     sun elevation is within [--gold-lo, --gold-hi], default -6..+6 deg.
               goldpeak picks its display frames from -3..+3, so this keeps the whole
               pickable window with 3 deg of margin — peaks stay re-derivable, not just
               the one frame that happened to win.
  * CADENCE    it is the earliest tick in its 15-minute UTC bin. Ticks drift off the
               5-minute grid, so binning (rather than "every 3rd file") is what actually
               yields ~15-minute spacing — and it is IDEMPOTENT: re-running removes
               nothing new, because the survivor of a bin is still that bin's earliest.

Whole ticks are kept or dropped together, so a surviving tick always has its complete
bracket triple — spot_ab.py requires len(v) >= 3 and would silently skip a torn one.

SAFETY. Dry-run is the default; deleting needs --apply. Filenames that do not parse are
never deleted. --protect-hours shields recent files (spot_ab.py reads the last 26 h, so
the live host wants 48). Stdlib only: this runs on ganesha too, where there is no venv.

  # what would go, no changes:
  python3 spot_prune.py --root ~/monitor/spot_lab/brackets
  # on the live host, shielding what the nightly A/B still needs:
  python3 spot_prune.py --root ~/monitor/spot_lab/brackets --protect-hours 48 --apply
"""
import argparse, collections, datetime, math, os, re, sys

CMU_LAT, CMU_LON = 40.4443, -79.9436
TICK_RE = re.compile(r"^(\d{8}_\d{6})_br\d+_E\d+\.png$")
U = datetime.timezone.utc


def sun_elev(dt, lat=CMU_LAT, lon=CMU_LON):
    """NOAA solar position -> apparent elevation, degrees. Agrees with the monitor's own
    logged sun= values to ~0.25 deg (checked against science.log, 2026-08-31)."""
    jd = dt.replace(tzinfo=U).timestamp() / 86400.0 + 2440587.5
    jc = (jd - 2451545.0) / 36525.0
    gmls = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    gmas = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    eeo = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    m = math.radians(gmas)
    seoc = (math.sin(m) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
            + math.sin(2 * m) * (0.019993 - 0.000101 * jc)
            + math.sin(3 * m) * 0.000289)
    stl = gmls + seoc
    omega = math.radians(125.04 - 1934.136 * jc)
    lam = math.radians(stl - 0.00569 - 0.00478 * math.sin(omega))
    seps = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    oblc = math.radians(seps + 0.00256 * math.cos(omega))
    decl = math.asin(math.sin(oblc) * math.sin(lam))
    y = math.tan(oblc / 2) ** 2
    gmlr, gmar = math.radians(gmls), math.radians(gmas)
    eqtime = 4 * math.degrees(
        y * math.sin(2 * gmlr) - 2 * eeo * math.sin(gmar)
        + 4 * eeo * y * math.sin(gmar) * math.cos(2 * gmlr)
        - 0.5 * y * y * math.sin(4 * gmlr) - 1.25 * eeo * eeo * math.sin(2 * gmar))
    tst = (dt.hour * 60 + dt.minute + dt.second / 60.0 + eqtime + 4 * lon) % 1440
    ha = math.radians(tst / 4 - 180 if tst / 4 > 0 else tst / 4 + 180)
    latr = math.radians(lat)
    zen = math.acos(math.sin(latr) * math.sin(decl)
                    + math.cos(latr) * math.cos(decl) * math.cos(ha))
    el = 90 - math.degrees(zen)
    t = math.tan(math.radians(el)) if el > -89 else 1e-9
    if el > 85:      r = 0.0
    elif el > 5:     r = 58.1 / t - 0.07 / t ** 3 + 0.000086 / t ** 5
    elif el > -0.575: r = 1735 + el * (-518.2 + el * (103.4 + el * (-12.79 + el * 0.711)))
    else:            r = -20.772 / t
    return el + r / 3600.0


def plan(root, gold_lo, gold_hi, bin_min, protect_hours):
    """-> (keep_ticks, drop_ticks, ticks{tick: [(name, bytes)]}, skipped, protected)"""
    ticks, skipped = collections.defaultdict(list), []
    cut = (datetime.datetime.now(U) - datetime.timedelta(hours=protect_hours)
           if protect_hours else None)
    protected = 0
    with os.scandir(root) as it:
        for e in it:
            if not e.is_file():
                continue
            m = TICK_RE.match(e.name)
            if not m:
                skipped.append(e.name)          # unrecognised: never a deletion candidate
                continue
            try:
                t = datetime.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S").replace(tzinfo=U)
            except ValueError:
                skipped.append(e.name)
                continue
            if cut and t >= cut:
                protected += 1
                continue
            ticks[m.group(1)].append((e.name, e.stat().st_size))

    when = {k: datetime.datetime.strptime(k, "%Y%m%d_%H%M%S").replace(tzinfo=U) for k in ticks}
    golden = {k for k, d in when.items() if gold_lo <= sun_elev(d) <= gold_hi}
    bins = collections.defaultdict(list)
    for k, d in when.items():
        bins[(d.date(), d.hour, d.minute // bin_min)].append(k)
    cadence = {min(v) for v in bins.values()}      # earliest tick in the bin == idempotent
    keep = golden | cadence
    return keep, set(ticks) - keep, ticks, skipped, protected, golden, cadence


def main():
    ap = argparse.ArgumentParser(description="Prune the bracket stack to golden hour + 15-min cadence.")
    ap.add_argument("--root", default=os.path.expanduser("~/monitor/spot_lab/brackets"))
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--protect-hours", type=float, default=0.0,
                    help="never touch files newer than this (use 48 on the live host)")
    ap.add_argument("--gold-lo", type=float, default=-6.0)
    ap.add_argument("--gold-hi", type=float, default=6.0)
    ap.add_argument("--bin", type=int, default=15, dest="bin_min")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(a.root):
        print("spot_prune: no such directory: %s" % a.root)
        return 0                                   # fail-soft: absent stack is not an error

    keep, drop, ticks, skipped, protected, golden, cadence = plan(
        a.root, a.gold_lo, a.gold_hi, a.bin_min, a.protect_hours)
    G = 1073741824.0
    kb = sum(s for t in keep for _, s in ticks[t])
    db = sum(s for t in drop for _, s in ticks[t])
    nk = sum(len(ticks[t]) for t in keep)
    nd = sum(len(ticks[t]) for t in drop)

    print("spot_prune: %s" % a.root)
    print("  keep   %5d ticks / %6d files / %7.1f GB  (golden %d, cadence %d, both %d)"
          % (len(keep), nk, kb / G, len(golden), len(cadence), len(golden & cadence)))
    print("  delete %5d ticks / %6d files / %7.1f GB" % (len(drop), nd, db / G))
    if protected:
        print("  protected (newer than %gh, untouched): %d files" % (a.protect_hours, protected))
    if skipped and not a.quiet:
        print("  unrecognised names kept: %d %s" % (len(skipped), skipped[:3]))

    # A day must never be emptied outright -- that would be a gap, not a thinning.
    by_day_keep = collections.Counter(t[:8] for t in keep)
    emptied = sorted({t[:8] for t in drop} - set(by_day_keep))
    if emptied:
        print("  REFUSING: these days would lose every frame: %s" % emptied[:5])
        return 1
    if not drop:
        print("  nothing to do (already pruned)")
        return 0
    if not a.apply:
        print("  DRY RUN -- pass --apply to delete")
        return 0

    freed = errs = 0
    for t in sorted(drop):
        for n, s in ticks[t]:
            try:
                os.remove(os.path.join(a.root, n))
                freed += s
            except OSError as e:
                errs += 1
                if errs <= 3:
                    print("  rm failed: %s (%s)" % (n, e))
    print("  DELETED %d files, freed %.1f GB%s"
          % (nd - errs, freed / G, (", %d errors" % errs) if errs else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
