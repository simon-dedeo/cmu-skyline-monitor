#!/bin/bash
# serve.sh — tiny localhost web server for the dashboard.
# The dashboard uses fetch(data.json), which the browser blocks over file://,
# so we serve ~/monitor over http on 127.0.0.1 only (not exposed to the network).
cd "$(dirname "$0")" || exit 1
exec /Users/proofsandreasons/monitor/venv/bin/python3 -m http.server 8787 --bind 127.0.0.1
