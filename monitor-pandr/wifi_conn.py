#!/Users/proofsandreasons/monitor/venv/bin/python3
"""wifi_conn.py -- 1 Hz sampler of the CONNECTED access point's RSSI (daemon).

Companion to wifi_log.py (5-min scan of all radios). Reads CoreWLAN's interface
values, which the driver already tracks for rate control: NO scan, no radio stall
(measured 2026-11-04: 4 reads/s with ping max 14 ms, same as idle). The driver
refreshes the value irregularly, roughly every 1-2 s, and it wanders ~5 dB within
30 s even with nobody moving, so 1 Hz is about the native rate -- faster just logs
repeats. Only the associated AP is visible this way; neighbours need the scan.
  * Sequoia: ssid()/bssid() come back None for a launchd process without Location
    Services permission; rssiValue()/noise/txrate/channel are unaffected. Association
    is detected by rssi != 0.

  * launchd com.lsm.wificonn, KeepAlive (this is a long-running loop, unlike the
    other one-shot ticks). Needs the venv python (PyObjC CoreWLAN lives there).
  * Output: wifi_conn/YYYY-MM-DD.csv (UTC day), columns ts, rssi, noise, tx_mbps, ch.
    SSID is written once per file header comment, not per row. ~86 400 rows x ~30 B =
    ~2.5 MB/day raw; yesterday's file is gzipped on rollover (~10x smaller), so the
    directory grows ~100 MB/yr. Nothing pruned.
  * Logs to monitor.log on start, association loss/regain, and errors -- not per row.
"""
import os, sys, time, gzip, shutil, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "wifi_conn")
LOG = os.path.join(HERE, "monitor.log")
PERIOD = float(os.environ.get("WIFI_CONN_PERIOD", "1.0"))

def log(msg):
    line = f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} [wificonn] {msg}"
    try: open(LOG, "a").write(line + "\n")
    except Exception: print(line, flush=True)

def gzip_old(day):
    """Compress every plain .csv in OUTDIR that is not today's."""
    for fn in os.listdir(OUTDIR):
        if fn.endswith(".csv") and fn[:-4] != day:
            p = os.path.join(OUTDIR, fn)
            try:
                with open(p, "rb") as f, gzip.open(p + ".gz", "wb", compresslevel=6) as g:
                    shutil.copyfileobj(f, g)
                os.remove(p); log(f"gzipped {fn}")
            except Exception as e:
                log(f"gzip {fn} failed: {e!r}")

def main():
    import CoreWLAN
    os.makedirs(OUTDIR, exist_ok=True)
    iface = CoreWLAN.CWWiFiClient.sharedWiFiClient().interface()
    log(f"start, period {PERIOD}s, iface {iface.interfaceName() if iface else None}")
    day, f, assoc = None, None, None
    next_t = time.time()
    while True:
        now = datetime.datetime.now(datetime.timezone.utc)
        d = now.strftime("%Y-%m-%d")
        if d != day:
            if f: f.close()
            day = d
            p = os.path.join(OUTDIR, day + ".csv")
            new = not os.path.exists(p) or os.path.getsize(p) == 0
            f = open(p, "a", buffering=1)      # line-buffered: a crash loses <1 row
            if new:
                f.write(f"# ssid={iface.ssid() if iface else ''} band={iface.wlanChannel().channelBand() if iface and iface.wlanChannel() else ''}\n")
                f.write("ts,rssi,noise,tx_mbps,ch\n")
            gzip_old(day)
        try:
            # Sequoia hides ssid()/bssid() from a launchd process without Location
            # permission (reads as None), but rssiValue() still works: a 0 means
            # not associated, so gate on that rather than on the name.
            rssi = int(iface.rssiValue()) if iface else 0
            if rssi == 0:
                if assoc is not False: log("not associated"); assoc = False
            else:
                ch = iface.wlanChannel()
                if assoc is not True:
                    log(f"associated {iface.ssid() or '(ssid hidden)'} ch{ch.channelNumber() if ch else '?'} {rssi} dBm"); assoc = True
                f.write(f"{now.strftime('%H:%M:%S')},{rssi},{int(iface.noiseMeasurement())},"
                        f"{int(iface.transmitRate())},{ch.channelNumber() if ch else ''}\n")
        except Exception as e:
            log(f"read failed: {e!r}"); time.sleep(5)
            iface = CoreWLAN.CWWiFiClient.sharedWiFiClient().interface()
        next_t += PERIOD
        time.sleep(max(0.0, next_t - time.time()))

if __name__ == "__main__":
    sys.exit(main())
