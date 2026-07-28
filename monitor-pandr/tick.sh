#!/bin/bash
# tick.sh — the entry point launchd (com.lsm.monitor) calls every 5 minutes.
#
# Camera access on Sequoia: the sky frame is captured by SkyCam.app, a signed
# bundle carrying NSCameraUsageDescription. Launched via `open` (see capture.sh),
# macOS attributes the TCC camera grant to the app itself, so no ssh->localhost hop
# is needed — this LaunchAgent runs in the GUI session (gui/501), where `open`
# reaches the console session (the same mechanism the kiosk uses to launch Chrome).
# (The old ssh hop was the Big-Sur imagesnap workaround; it does NOT grant camera
# on Sequoia and is retired with the old camera.)
exec bash "$HOME/monitor/capture.sh"
