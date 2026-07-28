# New machine setup — CMU Skyline Monitor on "pandr" (2026-07-17)

Migrated the monitor from the old 2015 MacBook Pro (`laboratorys-mbp`) to a
**MacBook Air M1 (macOS 15.5 Sequoia), host `pandr`**, user `proofsandreasons`.
This machine is now the live image-grabber / kiosk / uploader.

## What runs here
Four launchd LaunchAgents (in `~/Library/LaunchAgents/`, loaded in the GUI/Aqua session):
- **com.lsm.monitor** — every 300 s: `tick.sh` → ssh-localhost hop → `capture.sh`
  → `update.py` (weather via Open-Meteo@CMU + NWS forecast, solar/golden times,
  system health, camera frames) → archives + science log → uploads data.json/js +
  skycolor.js to santafe (throttled ~15 min).
- **com.lsm.goldscan** — every 150 s: `goldtick.sh`; no-op outside golden windows,
  captures/scores the sky frame during them and uploads the peak at window close.
- **com.lsm.monitor-web** — localhost:8787 dashboard server (KeepAlive).
- **com.lsm.kiosk** — opens the dashboard in TRUE fullscreen at login via **Google
  Chrome `--kiosk`** (borderless, no tabs/toolbar/title bar). Chrome fullscreens
  itself, so this needs NO Accessibility/TCC permission — fully unattended. (Safari
  was dropped for the kiosk: its fullscreen is a Cmd-Ctrl-F keystroke that macOS
  blocks for automation, so it stayed windowed with a visible border.)

## Toolchain (replaces the old machine's MacPorts /opt/local)
- Homebrew at `/opt/homebrew`; `imagesnap`, `libusb` via brew.
- Python venv at `~/monitor/venv` (Python 3.13, OpenCV 5.0, numpy 2.5, libusb1).
  All scripts point their shebang / `PY=` / `IMAGESNAP=` here.
- Xcode Command Line Tools installed.
- `ffmpeg` (8.1.2, brew) with VideoToolbox HW encoders (h264/hevc/prores) — for
  on-machine timelapse rendering.
- Google Chrome (brew cask) — used only for the fullscreen kiosk (`--kiosk`).

## Access / keys
- Passwordless SSH login + passwordless sudo (for VDCAssistant kill / usbreset /
  hubpower camera-recovery tools).
- `~/.ssh/id_ed25519` authorizes BOTH the localhost hop (camera TCC) AND santafe
  upload (on the old machine santafe used id_rsa; here it's id_ed25519).

## System settings applied
- Timezone → America/New_York (was Los_Angeles).
- `pmset -a sleep 0 disksleep 0 displaysleep 0 autorestart 1` (never sleeps; auto-boots
  after power loss). Auto-login is ON (proofsandreasons).
- Remote Login (ssh) + Screen Sharing enabled; screensaver idle time 0.

## State
- Old-machine image archives + old science.csv moved to
  `~/monitor/OLD-MACHINE-DATA-2026-07-17/` (safe to delete; originals live on the
  old machine). Public-continuity files kept in place: golden.js, goldindex.js,
  .golden_peak.json, .goldindex.json — so the site's featured golden image and
  history stay unbroken.
- Last-known sky frames restored with their original (Jul 16) mtime so the
  dashboards show the last real picture and the status light correctly reads
  "camera down" until a camera is attached.

## >>> WHEN THE NEW USB-3 CAMERA ARRIVES <<<
1. Plug it in. Identify it: `system_profiler SPUSBDataType` and
   `~/monitor/venv/bin/python3 -c 'import usb1; [print(hex(d.getVendorID()),hex(d.getProductID()),d.getProduct()) for d in usb1.USBContext().getDeviceList()]'`.
2. Update the vendor/product IDs + capture config in `expose.py` (old cam was
   0x0c45:0x0578 GS sky). Adjust resolution/HDR/exposure tables for the new sensor.
   Update the `-d "Arducam ..."` device name in `expose.py`/`update.py` (imagesnap).
3. **Camera TCC grant**: the capture runs under `sshd` via the localhost hop, so the
   FIRST camera access needs macOS to grant camera permission to the ssh path
   (`/usr/libexec/sshd-keygen-wrapper`). If frames are silently denied, open the
   camera once from a Terminal on the console (or approve the prompt) — same trick
   as the old machine. Never put scripts in ~/Desktop (TCC-blocked for launchd).
4. Re-aim with `~/monitor/aim.py` (served at http://127.0.0.1:8787/aim.html), then
   verify ROIs with `roi_overlay.py` and re-pin the drift baseline.
5. A modern USB-3 cam should avoid the old GS cam's hub-bandwidth wedges; still
   prefer a powered hub with real per-port switching if using one.

## Health-check commands
    launchctl list | grep lsm
    tail -20 ~/monitor/monitor.log
    curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/dashboard.html
    ~/monitor/venv/bin/python3 ~/monitor/update.py     # one manual tick
