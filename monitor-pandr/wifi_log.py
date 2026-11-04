#!/usr/bin/env python3
"""wifi_log.py -- log Wi-Fi RSSI of the strongest access points to wifi_rssi.csv.

Started 2026-11-04 (Simon): a fixed receiver can't map the room, but RSSI *change*
over time is a sensor -- occupancy/motion perturbs multipath, door open/closed is a
step, and long-run drift of a fixed AP may track weather. We log and see.

  * Source: `system_profiler SPAirPortDataType -json` (no sudo, no PyObjC, ~4 s; the
    old `airport` CLI is gone on Sequoia). It reports SSID, channel, RSSI, noise for
    every AP in range but NOT the BSSID, so the key is (band, channel): one physical
    radio broadcasts CMU-SECURE / CMU-DEVICE / CMU-GUEST on the same channel with the
    same RSSI, and we keep only one row per radio (the SSID we're connected to wins,
    else the first seen).
  * Cadence: every 5 min from launchd (com.lsm.wifi), same as the photo tick. A scan
    stalls the radio for ~1 s, which is why this is not every minute.
  * Size: top TOP_N radios (default 8) + the connected one => ~9 rows x 288 ticks x
    ~40 B = ~100 KB/day, ~40 MB/yr. Nothing is pruned.
  * CSV columns: ts (UTC ISO), conn (1 if this is the AP we're associated with),
    ssid, band (2/5/6 GHz), ch, width_mhz, rssi_dbm, noise_dbm.
"""
import os, sys, json, csv, re, subprocess, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "wifi_rssi.csv")
LOG = os.path.join(HERE, "monitor.log")
TOP_N = int(os.environ.get("WIFI_TOP_N", "8"))
HDR = ["ts", "conn", "ssid", "band", "ch", "width_mhz", "rssi_dbm", "noise_dbm"]

def log(msg):
    line = f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} [wifi] {msg}"
    print(line)
    try: open(LOG, "a").write(line + "\n")
    except Exception: pass

def parse_sn(s):
    # "-61 dBm / -90 dBm"
    m = re.findall(r"(-?\d+)\s*dBm", s or "")
    return (int(m[0]), int(m[1])) if len(m) >= 2 else (None, None)

def parse_ch(s):
    # "36 (5GHz, 80MHz)"
    m = re.match(r"\s*(\d+)\s*\((\d+)GHz,\s*(\d+)MHz\)", s or "")
    return (int(m[1]), int(m[2]), int(m[3])) if m else (None, None, None)

def row(ts, conn, n):
    rssi, noise = parse_sn(n.get("spairport_signal_noise"))
    ch, band, width = parse_ch(n.get("spairport_network_channel"))
    if rssi is None or ch is None: return None
    return dict(ts=ts, conn=conn, ssid=n.get("_name", ""), band=band, ch=ch,
                width_mhz=width, rssi_dbm=rssi, noise_dbm=noise)

def main():
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        raw = subprocess.run(["system_profiler", "SPAirPortDataType", "-json"],
                             capture_output=True, text=True, timeout=60).stdout
        iface = json.loads(raw)["SPAirPortDataType"][0]["spairport_airport_interfaces"][0]
    except Exception as e:
        log(f"scan failed: {e!r}"); return 0
    rows = {}
    cur = iface.get("spairport_current_network_information")
    if cur:
        r = row(ts, 1, cur)
        if r: rows[(r["band"], r["ch"])] = r
    for n in iface.get("spairport_airport_other_local_wireless_networks", []):
        r = row(ts, 0, n)
        if r: rows.setdefault((r["band"], r["ch"]), r)   # one row per physical radio
    keep = sorted(rows.values(), key=lambda r: (-r["conn"], -r["rssi_dbm"]))[:TOP_N + 1]
    new = not os.path.exists(OUT) or os.path.getsize(OUT) == 0
    with open(OUT, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HDR)
        if new: w.writeheader()
        for r in keep: w.writerow(r)
    c = next((r for r in keep if r["conn"]), None)
    log(f"{len(keep)} radios logged" + (f"; connected {c['ssid']} ch{c['ch']} {c['rssi_dbm']}/{c['noise_dbm']} dBm" if c else "; not associated"))
    return 0

if __name__ == "__main__":
    sys.exit(main())
