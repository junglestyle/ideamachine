#!/bin/bash
# Link Idea Machine's user units into ~/.config/systemd/user and enable them. Idempotent.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.config/systemd/user
for unit in ideamachine-db.service ideamachine-run.service ideamachine-run.timer; do
  ln -sf "$here/$unit" ~/.config/systemd/user/"$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now ideamachine-db.service ideamachine-run.timer
systemctl --user list-timers ideamachine-run.timer --no-pager
