#!/bin/bash
# aimer.sh [start|stop] — LOCAL live-streaming camera aimer, shown on the kiosk.
#   start: run SkyCam in --stream mode (continuous 1080 frames → ~/monitor/aim.jpg,
#          served by the local web server at http://localhost:8787/aim.html) and point
#          the kiosk Chrome at that page. Near-live (~0.3 s), no network round-trip.
#   stop:  kill the stream and return the kiosk to the dashboard.
# NOTE: the stream holds the camera continuously, so the 5-min monitor tick's own
#       capture will skip while aiming — that's fine; stop the aimer when done.
cd "$HOME/monitor" || exit 1
CHROME="/Applications/Google Chrome.app"
PROFILE="$HOME/monitor/.chrome-kiosk"
kioskto() {
  pkill -f "user-data-dir=$PROFILE" 2>/dev/null; sleep 1
  open -na "$CHROME" --args --kiosk "$1" --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check --disable-session-crashed-bubble \
    --hide-crash-restore-bubble --disable-infobars --noerrdialogs --password-store=basic >/dev/null 2>&1
}
case "$1" in
  stop)
    pkill -f "SkyCam.app/Contents/MacOS/skycap" 2>/dev/null
    rm -f aim.jpg aim.jpg.tmp
    kioskto "http://localhost:8787/dashboard.html"
    echo "aimer stopped; kiosk restored to dashboard"
    ;;
  *)
    pkill -f "SkyCam.app/Contents/MacOS/skycap" 2>/dev/null; sleep 1
    open -a "$HOME/monitor/SkyCam.app" --args --device "Elgato Facecam 4K" \
      --stream --res 1080 --interval 0.3 --out "$HOME/monitor/aim.jpg"
    sleep 2
    kioskto "http://localhost:8787/aim.html"
    echo "aimer streaming -> ~/monitor/aim.jpg ; kiosk showing http://localhost:8787/aim.html"
    ;;
esac
