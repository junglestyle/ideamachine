#!/usr/bin/env bash
# On eeyore, as your user: stop Idea Machine here and hand its database to the NAS.
#
# 1. On the NAS (as root): clone both repos and run install/nas.sh once. It creates the datasets, then stops and
#    asks for the database.
# 2. Here: this script. It stops the hourly run, dumps the database, and copies it into the NAS's backups dataset.
# 3. On the NAS: run install/nas.sh again. It restores, checks nothing would be re-sent to Claude, and schedules.
# 4. Here, once Lattice works on the NAS: systemctl --user disable --now lattice.service
#
# eeyore's database is left as it was, as a fallback, until you drop it.
set -euo pipefail
cd "$(dirname "$0")/.."

dump="$HOME/.local/share/ideamachine/move-$(date +%Y%m%dT%H%M%S).dump"
target="nas:/mnt/storage/ideamachine/backups/from-eeyore.dump"

ssh -o BatchMode=yes nas test -d /mnt/storage/ideamachine/backups \
    || { echo "Run install/nas.sh on the NAS first: it creates the backups dataset this copies into." >&2; exit 1; }

echo "==> Stopping the hourly run here (the NAS takes over)"
systemctl --user disable --now ideamachine-run.timer
while systemctl --user is-active -q ideamachine-run.service; do echo "waiting for a run in progress..."; sleep 10; done

echo "==> Dumping the database"
mkdir -p "$(dirname "$dump")"
(umask 077; podman exec ideamachine_db_1 pg_dump -U postgres -d im -Fc > "$dump")
ls -lh "$dump"

echo "==> Copying it to the NAS"
scp -q "$dump" "$target"
ssh nas "chmod 640 /mnt/storage/ideamachine/backups/from-eeyore.dump"
echo "copied to $target"

cat <<'EOF'

Next, on the NAS:  sudo /mnt/storage/ideamachine/repo/install/nas.sh
Then, once Lattice works there, here:  systemctl --user disable --now lattice.service
(The hourly run here is already off. To undo: systemctl --user enable --now ideamachine-run.timer)
EOF
