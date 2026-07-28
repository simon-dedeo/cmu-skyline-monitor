# CMU Courtyard Monitor

Simple, reliable 15-minute camera + weather monitor for the Lab for Social Minds
MacBook Pro. Deployed on the Mac at **`~/monitor/`**; this folder is a mirror of the
source. Full context is in [`../REPORT.md`](../REPORT.md).

## What it does
Every 15 minutes (launchd), it captures a frame from each Arducam, pulls Pittsburgh
weather from the US National Weather Service, computes sun/golden-hour times, writes
`data.json`, archives the frames locally, and uploads `courtyard.jpg` / `sky.jpg` /
`data.json` to `simon@santafe.santafe.edu:~/html/` (public at
https://sites.santafe.edu/~simon/). `dashboard.html` renders it all.

## Files
| File | Role |
|---|---|
| `com.lsm.monitor.plist` | LaunchAgent — `RunAtLoad` + `StartInterval` 900 s |
| `tick.sh` | launchd entry point; re-enters via `ssh localhost` so capture inherits sshd's camera (TCC) grant |
| `capture.sh` | one tick: run `update.py`, archive frames, `scp` to santafe |
| `update.py` | capture (via `expose.py`) + weather (Open-Meteo current @ CMU + NWS forecast) + solar + system → `data.json` |
| `gallery.py` | uploads timestamped hourly frames + builds `gallery.html` on santafe for remote overnight review |
| `solar.py` | sunrise/sunset/golden-hour (bundled copy; original in `~/Desktop/files`) |
| `dashboard.html` | the display — polls `data.json` + images every 60 s |
| `expose.py` | controlled single-frame capture: predictive exposure for the sky cam (flicker-free), auto for the low-light courtyard cam. Used by both `update.py` and `timelapse.sh` |
| `serve.sh` + `com.lsm.monitor-web.plist` | localhost-only web server (`127.0.0.1:8787`) so the dashboard's `fetch` works |
| `kiosk.sh` | opens Safari fullscreen to the dashboard (run in the GUI session) |
| `timelapse.sh` + `com.lsm.timelapse.plist` | hourly full-res frame from **both** cams → `timelapse/{sky,courtyard}/…` + upload `{sky,courtyard}_hourly.jpg` |

## Exposure (see ../REPORT.md §6b)
Predictive, not binary-search: brightness is ~linear in exposure, so `E' = E·(target/metered)` converges in 1–2 frames; a constant target keeps the timelapse flicker-free. **Both cameras use HDR exposure fusion** (bracket + Mertens blend) so sky and shadowed buildings/courtyard are both exposed. The **sky cam** brackets exposure via OpenCV+libusb; the **courtyard cam** brackets exposure `[1,2,4,8,16]` via a fresh `imagesnap` per frame (its live OpenCV session ignores manual control). Both fall back to a single auto frame near night. Camera capture needs the ssh→localhost hop for TCC. `hdr_test.py` is a standalone bracket-fusion prototype.

## Three launchd jobs
- `com.lsm.monitor` — 15-min capture + weather + upload
- `com.lsm.monitor-web` — always-on localhost web server for the dashboard
- `com.lsm.timelapse` — hourly GS sky-cam frame for a long-term cloud timelapse

## Put the dashboard on the screen
```bash
bash ~/monitor/kiosk.sh      # opens Safari fullscreen at localhost:8787/dashboard.html
```
Exit fullscreen with Cmd-Ctrl-F. Compile the timelapse with the ffmpeg line at the end of `timelapse.sh`.

## Two non-obvious gotchas (both cost real debugging)
1. **Don't put this in `~/Desktop`.** Desktop/Documents/Downloads are TCC-protected;
   launchd gets "Operation not permitted" there. Keep it in `~/monitor`.
2. **Camera under launchd needs the `ssh localhost` hop.** Camera access is granted to
   `sshd-keygen-wrapper`, not to launchd/bash, so a direct launchd `imagesnap` hangs.
   `tick.sh` routes through ssh to fix this.

## Weather & remote review
- Current conditions are **geolocated to CMU** via Open-Meteo (exact lat/lon); NWS supplies the worded forecast. NWS station obs is a fallback.
- Overnight frames are reviewable on the web (no SSH): **https://sites.santafe.edu/~simon/gallery.html** (updates hourly).

## Operate
```bash
launchctl list | grep com.lsm.monitor              # 0 = last tick OK
launchctl kickstart gui/$(id -u)/com.lsm.monitor   # run now
tail -f ~/monitor/monitor.log                       # watch
launchctl unload ~/Library/LaunchAgents/com.lsm.monitor.plist   # stop
launchctl load   ~/Library/LaunchAgents/com.lsm.monitor.plist   # start
```

## Install from scratch
```bash
mkdir -p ~/monitor && cp update.py solar.py capture.sh tick.sh dashboard.html ~/monitor/
chmod +x ~/monitor/{tick,capture}.sh ~/monitor/update.py
cp com.lsm.monitor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.lsm.monitor.plist
```
Upload target and cameras are configured at the top of `capture.sh` / `update.py`.
