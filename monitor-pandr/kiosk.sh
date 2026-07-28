#!/bin/bash
# kiosk.sh — put the dashboard on this Mac's screen in TRUE fullscreen.
#
# Uses Google Chrome's --kiosk mode: a borderless fullscreen window with no tabs,
# toolbar, or title bar. Chrome fullscreens ITSELF, so this needs NO Accessibility
# permission — unlike Safari, whose fullscreen is toggled by a Cmd-Ctrl-F keystroke
# that macOS blocks for automation ("not allowed to send keystrokes"), which is why
# the old Safari kiosk stayed windowed with a visible title bar.
#
# Run by launchd (com.lsm.kiosk) at login, or by hand:  bash ~/monitor/kiosk.sh
# Requires the localhost web server (com.lsm.monitor-web). Exit kiosk with Cmd-Q;
# the launchd job reopens it at the next login. Falls back to Safari (windowed) if
# Chrome is somehow absent.
URL="http://localhost:8787/dashboard.html"

# When launched by launchd at login, wait for the web server to come up first.
sleep "${KIOSK_STARTUP_DELAY:-0}"

APP="/Applications/Google Chrome.app"
PROFILE="$HOME/monitor/.chrome-kiosk"

if [ -d "$APP" ]; then
  # Kill any prior kiosk instance on this profile so a re-run/relaunch is clean.
  /usr/bin/pkill -f "user-data-dir=$PROFILE" 2>/dev/null
  sleep 1
  # Force a "clean exit" flag so Chrome never shows a restore-pages bubble after
  # an unclean shutdown (e.g. a power-loss reboot) — the bubble would sit over the
  # dashboard until dismissed.
  PREF="$PROFILE/Default/Preferences"
  if [ -f "$PREF" ]; then
    /usr/bin/sed -i "" -e 's/"exit_type":"[^"]*"/"exit_type":"Normal"/' \
                       -e 's/"exited_cleanly":false/"exited_cleanly":true/' "$PREF" 2>/dev/null
  fi
  # Retire the old Safari kiosk if it's still up.
  /usr/bin/osascript -e 'tell application "Safari" to quit' 2>/dev/null

  /usr/bin/open -na "$APP" --args \
    --kiosk "$URL" \
    --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check \
    --disable-session-crashed-bubble --hide-crash-restore-bubble \
    --disable-infobars --noerrdialogs --disable-features=Translate \
    --password-store=basic --use-mock-keychain \
    --overscroll-history-navigation=0
  echo "Chrome kiosk launched (true fullscreen). Quit with Cmd-Q; launchd reopens it at login."
else
  echo "Google Chrome not found — Safari fallback (windowed; true fullscreen needs Accessibility)."
  open -a Safari "$URL"
fi
