#!/usr/bin/env bash
# TrueNAS SCALE: datasets, secrets, images, Postgres, Lattice, the hourly run and nightly backups.
# Run as root from the cloned repo. Safe to re-run; also how updates are deployed.
#
# Moving from eeyore: run this once (it stops and asks for the database), then install/move-to-nas.sh on eeyore,
# then this again. FRESH=1 starts with an empty database instead.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

require_root

say "Checking Docker and the repos"
docker info >/dev/null 2>&1 || die "Docker isn't running. Set the Apps pool in the TrueNAS UI first."
[ -d "$HEARSAY_STREAM" ] || die "no Hearsay stream at $HEARSAY_STREAM (install Hearsay first)"
[ -f "$NAS_LATTICE_REPO/pyproject.toml" ] || die "Lattice isn't cloned. Run:
    git clone https://github.com/junglestyle/lattice.git $NAS_LATTICE_REPO"

say "Datasets under $NAS_POOL_DATASET"
# midclt (not raw zfs) so the TrueNAS UI knows about them.
for ds in "$NAS_POOL_DATASET" "$NAS_POOL_DATASET/config" "$NAS_POOL_DATASET/pg" "$NAS_POOL_DATASET/logs" \
          "$NAS_POOL_DATASET/backups"; do
    if zfs list -H -o name "$ds" >/dev/null 2>&1; then
        echo "exists: $ds"
    else
        midclt call pool.dataset.create "{\"name\": \"$ds\"}" >/dev/null
        echo "created: $ds"
    fi
done
[ -d "$NAS_CONFIG" ] && [ -d "$NAS_PG" ] && [ -d "$NAS_LOGS" ] && [ -d "$NAS_BACKUPS" ] \
    || die "datasets not mounted under $NAS_ROOT"
chown root:root "$NAS_CONFIG"; chmod 700 "$NAS_CONFIG"
chown "root:$APPS_GID" "$NAS_LOGS"; chmod 750 "$NAS_LOGS"
# Backups hold everyone's words: root and the apps group only. setgid + group-writable so a member of the apps
# group (the operator, over ssh) can drop the database from eeyore here.
chown "root:$APPS_GID" "$NAS_BACKUPS"; chmod 2770 "$NAS_BACKUPS"
# pg: Postgres runs as the apps user (install/compose.yaml). Data from before that change belongs to uid 999
# (`netdata` on TrueNAS); hand it over once, with the database stopped.
if [ "$(stat -c %u "$NAS_PG")" != "$APPS_UID" ]; then
    "${COMPOSE[@]}" stop db >/dev/null 2>&1 || true
    chown -R "$APPS_UID:$APPS_GID" "$NAS_PG"
    echo "pg now belongs to the apps user"
fi
chmod 700 "$NAS_PG"

say "Secrets in $NAS_CONFIG"
env_ensure "$NAS_DB_ENV" POSTGRES_PASSWORD "$(openssl rand -hex 24)"
env_ensure "$NAS_DB_ENV" IM_PIPELINE_PASSWORD "$(openssl rand -hex 24)"
env_ensure "$NAS_DB_ENV" LATTICE_APP_PASSWORD "$(openssl rand -hex 24)"
env_ensure "$NAS_PIPELINE_ENV" IM_DATABASE_URL \
    "postgresql://im_pipeline:$(env_get "$NAS_DB_ENV" IM_PIPELINE_PASSWORD)@db:5432/im"
if [ -z "$(env_get "$NAS_PIPELINE_ENV" ANTHROPIC_API_KEY)" ]; then
    # Manual: the key is in the operator's Anthropic console (or in eeyore's .env).
    read -rsp "Anthropic API key for idea extraction (input hidden): " key; echo
    env_ensure "$NAS_PIPELINE_ENV" ANTHROPIC_API_KEY "$key"
fi
env_ensure "$NAS_LATTICE_ENV" LATTICE_DATABASE_URL \
    "postgresql://lattice_app:$(env_get "$NAS_DB_ENV" LATTICE_APP_PASSWORD)@db:5432/im"
env_ensure "$NAS_LATTICE_ENV" LATTICE_TOKEN "$(openssl rand -base64 24 | tr -d '/+=')"

say "Building images"
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true
IM_COMMIT="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
"${COMPOSE[@]}" --profile tools build --build-arg "IM_COMMIT=$IM_COMMIT"

say "Postgres"
# Its first start runs dev/init as the postgres user, which can't read a clone made under a 077 umask.
chmod -R a+rX "$REPO_DIR/dev/init"
"${COMPOSE[@]}" up -d --wait db
if [ -z "$(db_sql "SELECT to_regclass('im.schema_migrations')")" ]; then
    if [ -f "$NAS_MOVE_DUMP" ]; then
        say "Restoring the database from eeyore ($NAS_MOVE_DUMP)"
        # Into the empty im and pub schemas the fresh database already has (dev/init, owned by the right roles):
        # pg_restore -n brings a schema's objects, not the schema itself.
        "${COMPOSE[@]}" exec -T db pg_restore -U postgres -d im -n im -n pub --exit-on-error < "$NAS_MOVE_DUMP"
        echo "restored"
    elif [ "${FRESH:-0}" = 1 ]; then
        echo "starting with an empty database (FRESH=1)"
    else
        die "no database yet. To move from eeyore, run install/move-to-nas.sh there (it drops the database at
    $NAS_MOVE_DUMP), then re-run this. To start empty instead: sudo FRESH=1 $0"
    fi
fi
im migrate

say "Checking that nothing already sent to Claude would be sent again"
# After a move, a different time zone or locale would change every payload: the next run would re-send every
# episode and orphan my verdicts. Stop before the cron job exists.
im pending --expect-none-resent || die "payloads differ from what was sent before (time zone?). Nothing is
    scheduled yet; fix this before re-running."

say "Lattice"
"${COMPOSE[@]}" up -d --build lattice
for _ in $(seq 30); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8790/login)" = 200 ] && break
    sleep 1
done
[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8790/login)" = 200 ] || die "Lattice isn't answering"
echo "answering on 127.0.0.1:8790"

say "Publishing Lattice on the tailnet (tailscale serve, HTTPS port $LATTICE_HTTPS_PORT)"
# The Tailscale app (TrueNAS UI: Apps) runs tailscaled in a container on the host network. Tailnet only: never
# `tailscale funnel`, which would put it on the internet.
ts="$(docker ps --format '{{.Names}} {{.Image}}' | awk 'tolower($2) ~ /tailscale/ {print $1; exit}')"
if [ -n "$ts" ]; then
    docker exec "$ts" tailscale serve --bg --https="$LATTICE_HTTPS_PORT" http://127.0.0.1:8790 >/dev/null
    host="$(docker exec "$ts" tailscale status --json | python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    LATTICE_URL="https://$host:$LATTICE_HTTPS_PORT/"
    echo "$LATTICE_URL"
else
    LATTICE_URL="(not published: no Tailscale container found)"
    echo "No Tailscale container found. Publish it by hand: tailscale serve --bg --https=$LATTICE_HTTPS_PORT http://127.0.0.1:8790"
fi

say "Hourly run at :45 and nightly backups (TrueNAS cron jobs)"
# :45: after Hearsay's transcription (:00, on eeyore) and reprocess (:30). flock skips a run while one is going.
cron_ensure "ideamachine run" 45 "*" \
    "( (date -Is; flock -n /run/ideamachine-run.lock ${COMPOSE[*]} run --rm -T run </dev/null) >> $NAS_LOGS/run.log 2>&1 )"
# Your labels, verdicts, stars and pins are the irreplaceable part. Two weeks of nightly dumps (ZFS snapshots of
# the pg dataset are a second line). No % in the command: cron would treat it as a newline.
cron_ensure "ideamachine backup" 15 3 \
    "( (date -Is; ${COMPOSE[*]} exec -T db pg_dump -U postgres -d im -Fc > $NAS_BACKUPS/im-\$(date -I).dump && find $NAS_BACKUPS -name 'im-*.dump' -mtime +14 -delete) >> $NAS_LOGS/backup.log 2>&1 )"
echo "logs: $NAS_LOGS/run.log, $NAS_LOGS/backup.log"

say "Done"
cat <<EOF
Idea Machine runs hourly at :45 (log: $NAS_LOGS/run.log). One run now, to see it work:
    sudo ${COMPOSE[*]} run --rm run
Lattice: $LATTICE_URL
    token: $(env_get "$NAS_LATTICE_ENV" LATTICE_TOKEN)
    (in Safari on the phone: open it, log in, Share -> Add to Home Screen)
EOF
