#!/bin/bash
# tick.sh — the entry point launchd calls every 15 minutes.
#
# Why the ssh-to-localhost hop:
#   macOS gates camera access with TCC (privacy). On this machine camera
#   access is granted to /usr/libexec/sshd-keygen-wrapper (the ssh login
#   path) and to Terminal -- but NOT to launchd/bash. A launchd-spawned
#   imagesnap is therefore silently denied and hangs until timeout.
#   Re-entering through ssh makes the capture run under sshd, which HAS
#   the camera grant, so imagesnap succeeds. (Verified 2026-07-09.)
exec /usr/bin/ssh \
  -i "$HOME/.ssh/id_ed25519" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=accept-new \
  -o ConnectTimeout=25 \
  localhost 'bash $HOME/monitor/capture.sh'
