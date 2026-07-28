#!/bin/bash
# kiosk.sh — put the dashboard on this Mac's screen, fullscreen.
# Run this ON the machine's GUI session (e.g. from Terminal, or `open`-triggered).
#
#   bash ~/monitor/kiosk.sh
#
# Requires the localhost web server (com.lsm.monitor-web) to be running.
# First run may ask to allow Terminal to control "System Events" (Accessibility)
# — that's what lets it toggle fullscreen. Allow it once.
URL="http://localhost:8787/dashboard.html"

open -a Safari "$URL"
sleep 2
osascript <<'APPLESCRIPT'
tell application "Safari" to activate
delay 0.5
tell application "System Events"
    -- enter full screen (View > Enter Full Screen = Cmd-Ctrl-F)
    keystroke "f" using {command down, control down}
end tell
APPLESCRIPT
echo "Dashboard opened fullscreen. Exit full screen with Cmd-Ctrl-F; move mouse to top for the menu bar."
