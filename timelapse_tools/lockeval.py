import csv, statistics as st
rows = list(csv.DictReader(open("/tmp/lockprobe.csv")))
for r in rows:
    for k in r:
        if k not in ("ts", "br"): r[k] = float(r[k])
day = [r for r in rows if r["level"] >= 60]
acc = [r for r in day if r["pk"] >= 0.45 and r["L12"] >= 0.08]
old = [r for r in day if r["pk"] >= 0.45 and r["m8"] >= 0.08]
print("daytime n=%d   proposed-gate accepted=%d (%.1f%%)   current-gate accepted=%d (%.1f%%)"
      % (len(day), len(acc), 100*len(acc)/len(day), len(old), 100*len(old)/len(day)))
print()
print("=== offsets the lock would apply (px, relative to the velocity prediction) ===")
for k in ("lx", "ly"):
    v = sorted(r[k] for r in acc); q = lambda p: v[int(p*(len(v)-1))]
    print("  %s: median %+.1f  p10 %+.1f  p90 %+.1f  min %+.1f  max %+.1f"
          % (k, st.median(v), q(.1), q(.9), v[0], v[-1]))
edge = sum(1 for r in acc if abs(r["lx"]) >= 25 or abs(r["ly"]) >= 25)
print("  sitting at the +/-25 px clamp edge: %d/%d (%.1f%%)" % (edge, len(acc), 100*edge/len(acc)))
print()
print("=== temporal coherence: within-hour scatter of the applied offset ===")
byh = {}
for r in acc: byh.setdefault(r["ts"][:13], []).append((r["lx"], r["ly"]))
print(" hour             n   med_lx  med_ly   MAD_lx  MAD_ly")
sx, sy = [], []
for h in sorted(byh):
    v = byh[h]
    mx = st.median([a for a, b in v]); my = st.median([b for a, b in v])
    ax = st.median([abs(a-mx) for a, b in v]); ay = st.median([abs(b-my) for a, b in v])
    sx.append(ax); sy.append(ay)
    print(" %s  %3d   %+6.1f  %+6.1f   %5.1f  %5.1f" % (h, len(v), mx, my, ax, ay))
print()
print(" median within-hour MAD: lx %.1f px, ly %.1f px" % (st.median(sx), st.median(sy)))
print(" (velocity is 0.77,-2.91 px/DAY, so a coherent locator should show ~0 px/hr drift)")
print()
print("=== does the proposed gate ever admit a genuinely ambiguous surface? ===")
riv = [r for r in day if r["L12"] < 1.0]   # a real rival local max exists
print(" daytime frames with a real rival local max: %d/%d (%.1f%%)" % (len(riv), len(day), 100*len(riv)/len(day)))
if riv:
    v = sorted(r["L12"] for r in riv); q = lambda p: v[int(p*(len(v)-1))]
    print("  their L12 margin: median %+.3f  p10 %+.3f  p90 %+.3f" % (st.median(v), q(.1), q(.9)))
    print("  of those, rejected by L12>=0.08: %d" % sum(1 for r in riv if r["L12"] < 0.08))
