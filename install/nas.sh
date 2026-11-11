#!/usr/bin/env bash
# TrueNAS SCALE: datasets, secrets, images, Postgres, the web view, the run as a corral kind, and nightly backups.
# Run as root (sudo) from admin's clone in /mnt/fast/repos. Safe to re-run; also how updates are deployed.
#
# Everything lives on the NVMe pool (fast/ideamachine). Before 2026-10-15 it was on storage/ideamachine: when fast
# has no database yet and storage has one, this copies it over once (zfs send | recv) and leaves storage as it was.
#
# Moving from eeyore: run this once (it stops and asks for the database), then install/move-to-nas.sh on eeyore,
# then this again. FRESH=1 starts with an empty database instead.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

require_root

say "Checking Docker and the clone"
docker info >/dev/null 2>&1 || die "Docker isn't running. Set the Apps pool in the TrueNAS UI first."
# Clones belong to admin: git as root in them trips "dubious ownership", and root-owned files block admin's pulls.
[ "$(stat -c %U "$REPO_DIR")" != root ] || die "$REPO_DIR belongs to root. Run this from admin's clone. As admin:
    git clone https://github.com/junglestyle/ideamachine.git /mnt/fast/repos/ideamachine
then: sudo /mnt/fast/repos/ideamachine/install/nas.sh"
echo "ideamachine: $REPO_DIR"

say "Hearsay's stream"
pick_hearsay_stream
echo "using $HEARSAY_STREAM ($HEARSAY_STREAM_WHY)"
# compose.yaml reads it from here (compose loads install/.env, next to it, on every command, including cron's).
cat > "$REPO_DIR/install/.env" <<EOF
# Written by install/nas.sh on every run: the pool Hearsay is on can change.
HEARSAY_STREAM=$HEARSAY_STREAM
EOF
chmod 644 "$REPO_DIR/install/.env"

say "Datasets under $NAS_POOL_DATASET"
if ! zfs list -H -o name "$NAS_POOL_DATASET" >/dev/null 2>&1; then
    # ZFS won't mount over a non-empty directory (e.g. a folder another install made here first).
    [ -z "$(ls -A "$NAS_ROOT" 2>/dev/null)" ] || die "$NAS_ROOT exists and isn't empty, but isn't a dataset. Move it
    aside (sudo mv $NAS_ROOT $NAS_ROOT.old), then re-run this."
    # midclt (not raw zfs) so the TrueNAS UI knows about them.
    midclt call pool.dataset.create "{\"name\": \"$NAS_POOL_DATASET\"}" >/dev/null
    echo "created: $NAS_POOL_DATASET"
fi

ROWS_BEFORE="$NAS_ROOT/rowcounts-before-move.txt"
ROWS_AFTER="$NAS_ROOT/rowcounts-after-move.txt"
if ! zfs list -H -o name "$NAS_POOL_DATASET/pg" >/dev/null 2>&1 && [ -f "$OLD_ROOT/pg/PG_VERSION" ]; then
    say "Moving from $OLD_POOL_DATASET to $NAS_POOL_DATASET (storage is left as it is)"
    # Nothing may write while it's copied. cron_ensure (below) switches the jobs back on.
    cron_disable "ideamachine run"
    cron_disable "ideamachine backup"
    echo "waiting for a run in progress, if any"
    flock "$RUN_LOCK" true
    # Count rows in storage's database, for checking the copy. From an earlier attempt that stopped partway, keep
    # the first count: nothing has written since.
    docker container inspect "$DB_CONTAINER" >/dev/null 2>&1 || die "no $DB_CONTAINER container to count rows in"
    docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' "$DB_CONTAINER" | grep -F "$OLD_ROOT/pg" >/dev/null \
        || die "$DB_CONTAINER doesn't use $OLD_ROOT/pg"
    if [ ! -s "$ROWS_BEFORE" ]; then
        docker start "$DB_CONTAINER" >/dev/null
        db_wait
        row_counts > "$ROWS_BEFORE.new"
        mv "$ROWS_BEFORE.new" "$ROWS_BEFORE"
    fi
    echo "counted rows in $(wc -l < "$ROWS_BEFORE") tables: $ROWS_BEFORE"
    for c in ideamachine-web "$DB_CONTAINER"; do docker stop "$c" >/dev/null 2>&1 || true; done
    echo "stopped the web view and the database"

    # One snapshot of all three, taken together; zfs send -p brings each dataset's properties along. pg goes last:
    # its absence is what says the move isn't done.
    snap="to-fast-$(date +%Y%m%d-%H%M%S)"
    zfs snapshot "$OLD_POOL_DATASET/config@$snap" "$OLD_POOL_DATASET/backups@$snap" "$OLD_POOL_DATASET/pg@$snap"
    for child in config backups pg; do
        if zfs list -H -o name "$NAS_POOL_DATASET/$child" >/dev/null 2>&1; then
            echo "already copied: $child"
            continue
        fi
        zfs send -p "$OLD_POOL_DATASET/$child@$snap" | zfs recv -x mountpoint "$NAS_POOL_DATASET/$child"
        echo "copied: $OLD_POOL_DATASET/$child -> $NAS_POOL_DATASET/$child ($(zfs list -H -o used "$NAS_POOL_DATASET/$child"))"
    done
    # logs/ held log output (now in the journal) and files kept by hand (an `im trial` report, a text export).
    # Those move to saved/; the old *.log files stay on storage.
    mkdir -p "$NAS_SAVED"
    for f in "$OLD_ROOT"/logs/*; do
        [ -f "$f" ] && [ "${f%.log}" = "$f" ] && [ ! -e "$NAS_SAVED/$(basename "$f")" ] || continue
        cp -a "$f" "$NAS_SAVED/"
        echo "copied: logs/$(basename "$f") -> saved/"
    done
fi

# On a fresh install, there was nothing to copy: create them empty.
for ds in "$NAS_POOL_DATASET/config" "$NAS_POOL_DATASET/pg" "$NAS_POOL_DATASET/backups"; do
    if zfs list -H -o name "$ds" >/dev/null 2>&1; then
        echo "exists: $ds"
    else
        midclt call pool.dataset.create "{\"name\": \"$ds\"}" >/dev/null
        echo "created: $ds"
    fi
done
[ -d "$NAS_CONFIG" ] && [ -d "$NAS_PG" ] && [ -d "$NAS_BACKUPS" ] || die "datasets not mounted under $NAS_ROOT"
chown root:root "$NAS_CONFIG"; chmod 700 "$NAS_CONFIG"
# Backups hold everyone's words: root and the apps group only. setgid + group-writable so a member of the apps
# group (the operator, over ssh) can drop the database from eeyore here.
chown "root:$APPS_GID" "$NAS_BACKUPS"; chmod 2770 "$NAS_BACKUPS"
# saved/: root and apps only (it quotes people). Group-writable, so `im audit-* sheet /saved/<new folder>` can make it.
mkdir -p "$NAS_SAVED"; chown "root:$APPS_GID" "$NAS_SAVED"; chmod 770 "$NAS_SAVED"
# pg: Postgres runs as the apps user (install/compose.yaml). Data from before that change belongs to uid 999
# (`netdata` on TrueNAS); hand it over once, with the database stopped.
if [ "$(stat -c %u "$NAS_PG")" != "$APPS_UID" ]; then
    "${COMPOSE[@]}" stop db >/dev/null 2>&1 || true
    chown -R "$APPS_UID:$APPS_GID" "$NAS_PG"
    echo "pg now belongs to the apps user"
fi
chmod 700 "$NAS_PG"

say "Retiring Lattice (removed from the deploy 2026-11-11; docs/deploy.md)"
# Lattice, its public MCP listener and the Cloudflare tunnel to it, their images, Lattice's tailnet address, and its
# config, whose database URL and token the web view keeps under its own names. Does nothing once they're gone.
for c in lattice-tunnel lattice; do
    docker container inspect "$c" >/dev/null 2>&1 || continue
    docker rm -f "$c" >/dev/null
    echo "removed container $c"
done
for image in lattice cloudflare/cloudflared:2026.9.3; do
    docker image inspect "$image" >/dev/null 2>&1 || continue
    docker image rm "$image" >/dev/null
    echo "removed image $image"
done
# The Tailscale app (TrueNAS UI: Apps) runs tailscaled in a container on the host network.
ts="$(docker ps --format '{{.Names}} {{.Image}}' | awk 'tolower($2) ~ /tailscale/ {print $1; exit}')"
lattice_https_port=8444
if [ -n "$ts" ] && docker exec "$ts" tailscale serve status --json | python3 -c '
import json, sys
try:
    tcp = json.load(sys.stdin).get("TCP") or {}
except ValueError:
    tcp = {}
sys.exit(0 if sys.argv[1] in tcp else 1)' "$lattice_https_port"; then
    docker exec "$ts" tailscale serve --https="$lattice_https_port" off >/dev/null
    echo "removed Lattice's address on the tailnet (tailscale serve, HTTPS port $lattice_https_port)"
fi
if [ -f "$NAS_CONFIG/lattice.env" ]; then
    if [ -f "$NAS_WEB_ENV" ]; then
        echo "both $NAS_CONFIG/lattice.env and $NAS_WEB_ENV exist: using $NAS_WEB_ENV; remove the other by hand"
    else
        mv "$NAS_CONFIG/lattice.env" "$NAS_WEB_ENV"
        echo "moved $NAS_CONFIG/lattice.env to $NAS_WEB_ENV"
    fi
fi
env_rename "$NAS_WEB_ENV" LATTICE_DATABASE_URL IM_WEB_DATABASE_URL
env_rename "$NAS_WEB_ENV" LATTICE_TOKEN IM_WEB_TOKEN
for key in LATTICE_PUBLIC_URL LATTICE_MCP_ALLOW LATTICE_DEMO_HIDE; do env_delete "$NAS_WEB_ENV" "$key"; done
env_rename "$NAS_DB_ENV" LATTICE_APP_PASSWORD IM_WEB_PASSWORD
[ ! -e "$NAS_CONFIG/cloudflared.env" ] || { rm -f "$NAS_CONFIG/cloudflared.env"; echo "removed the tunnel's token"; }
echo "ok"

say "Secrets in $NAS_CONFIG"
env_ensure "$NAS_DB_ENV" POSTGRES_PASSWORD "$(openssl rand -hex 24)"
env_ensure "$NAS_DB_ENV" IM_PIPELINE_PASSWORD "$(openssl rand -hex 24)"
env_ensure "$NAS_DB_ENV" IM_WEB_PASSWORD "$(openssl rand -hex 24)"   # the web view's role, lattice_app (058)
env_ensure "$NAS_PIPELINE_ENV" IM_DATABASE_URL \
    "postgresql://im_pipeline:$(env_get "$NAS_DB_ENV" IM_PIPELINE_PASSWORD)@db:5432/im"
if [ -z "$(env_get "$NAS_PIPELINE_ENV" ANTHROPIC_API_KEY)" ]; then
    # Manual: the key is in the operator's Anthropic console (or in eeyore's .env).
    read -rsp "Anthropic API key for idea extraction (input hidden): " key; echo
    env_ensure "$NAS_PIPELINE_ENV" ANTHROPIC_API_KEY "$key"
fi
# OPENROUTER_API_KEY (optional, by hand in pipeline.env): other providers' models, for `im trial` and edge drafts.
# Jev (im.jev) goes through OpenRouter on its key, with zero retention; TYPESAFE_API_KEY (optional) calls TypeSafe
# directly instead.
env_ensure "$NAS_WEB_ENV" IM_WEB_DATABASE_URL \
    "postgresql://lattice_app:$(env_get "$NAS_DB_ENV" IM_WEB_PASSWORD)@db:5432/im"
env_ensure "$NAS_WEB_ENV" IM_WEB_TOKEN "$(openssl rand -base64 24 | tr -d '/+=')"

# Idea Machine's own config: readable by the pipeline (the apps group), not secret, but personal, so not in the repo.
mkdir -p "$NAS_CONFIG/im"
chown "root:$APPS_GID" "$NAS_CONFIG/im"; chmod 750 "$NAS_CONFIG/im"
if [ ! -f "$NAS_CONFIG/im/im.toml" ]; then
    cat > "$NAS_CONFIG/im/im.toml" <<'TOML'
# Idea Machine's config. Every key and its default is in src/im/config.py. Kept out of the repo: it's personal.
# Changing extract.prompt re-reads every episode with Claude, so try a prompt first: im trial <prompt>. A stage's
# model and effort are part of its version: changing them re-asks sensitivity about every idea, and themes about
# every idea that fits no theme; the matcher and the digest use them from then on.
[extract]
# prompt = "v5.3"

[match]
# model = "claude-opus-5-5"
# effort = "low"

[themes]
# model = "claude-opus-5-5"
# effort = "low"

[sensitivity]
# model = "claude-opus-5-5"
# effort = "low"

[digest]
# model = "claude-opus-5-5"
# effort = "low"
TOML
    echo "created $NAS_CONFIG/im/im.toml"
fi
chown "root:$APPS_GID" "$NAS_CONFIG/im/im.toml"; chmod 640 "$NAS_CONFIG/im/im.toml"

# From / as cron does, so this is the stream the hourly run will mount. (After the secrets: compose reads env files.)
(cd / && "${COMPOSE[@]}" --profile tools config) | grep -F "source: $HEARSAY_STREAM" >/dev/null \
    || die "compose doesn't pick up $REPO_DIR/install/.env"

say "Building images"
IM_COMMIT="$(git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
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
# After a move from storage: the same rows as before, checked before anything (migrations, the web view, the run)
# writes.
if [ -f "$ROWS_BEFORE" ] && [ ! -f "$ROWS_AFTER" ]; then
    say "Checking the copy against storage's database (row counts of every table)"
    row_counts > "$ROWS_AFTER.new"
    diff "$ROWS_BEFORE" "$ROWS_AFTER.new" || die "row counts differ (above: < storage, > fast). Nothing is
    scheduled and the web view is stopped. storage/ideamachine is untouched."
    mv "$ROWS_AFTER.new" "$ROWS_AFTER"
    echo "all $(wc -l < "$ROWS_AFTER") tables match: $(awk -F'|' '{n += $2} END {print n}' "$ROWS_AFTER") rows"
fi
# The per-database setup ran once, when the volume was made; it's safe to re-run, and this brings what was added to
# it since (jit off) to a database made before.
"${COMPOSE[@]}" exec -T db psql -q -v ON_ERROR_STOP=1 -U postgres -d im \
    -f /docker-entrypoint-initdb.d/bootstrap-db.sql.in
im migrate

say "Retiring policy C's names role and key (decision 0008, amended 2026-11-11)"
# The GPU names job read segments and wrote names as role im_names, through `ssh nas` to Postgres on this box's
# loopback. DROP OWNED revokes what it was granted in each database, then the role goes, with its password, policy
# C's key and the job's connection files. Does nothing once they're gone.
if [ "$(db_sql "SELECT 1 FROM pg_roles WHERE rolname = 'im_names'")" = 1 ]; then
    for d in $(db_sql "SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate"); do
        "${COMPOSE[@]}" exec -T db psql -q -v ON_ERROR_STOP=1 -U postgres -d "$d" -c "DROP OWNED BY im_names"
    done
    db_sql "DROP ROLE im_names" >/dev/null
    echo "dropped role im_names"
fi
env_delete "$NAS_DB_ENV" IM_NAMES_PASSWORD
env_delete "$NAS_PIPELINE_ENV" IM_NAME_KEY
for f in "$NAS_ROOT/gpu/names.env" "$NAS_ROOT/gpu/names-rebuild.env"; do
    [ ! -e "$f" ] || { rm -f "$f"; echo "removed $f"; }
done
[ ! -d "$NAS_ROOT/gpu" ] || rmdir "$NAS_ROOT/gpu" 2>/dev/null || true
echo "ok"

say "Checking that nothing already sent to Claude would be sent again"
# After a move, a different time zone or locale would change every payload: the next run would re-send every
# episode and orphan my verdicts. Stop before the cron job exists.
im pending --expect-none-resent || die "payloads differ from what was sent before (time zone?). Nothing is
    scheduled yet; fix this before re-running."

say "The web view (im web; tailscale serve, HTTPS port $WEB_HTTPS_PORT)"
"${COMPOSE[@]}" up -d web
for _ in $(seq 30); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8792/login)" = 200 ] && break
    sleep 1
done
[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8792/login)" = 200 ] || die "im web isn't answering"
echo "answering on 127.0.0.1:8792"
# Tailnet only: never `tailscale funnel`, which would put it on the internet.
if [ -n "$ts" ]; then
    docker exec "$ts" tailscale serve --bg --https="$WEB_HTTPS_PORT" http://127.0.0.1:8792 >/dev/null
    ts_host="$(docker exec "$ts" tailscale status --json \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    WEB_URL="https://$ts_host:$WEB_HTTPS_PORT/"
    echo "$WEB_URL"
else
    WEB_URL="(not published: no Tailscale container found)"
    echo "No Tailscale container found. Publish it by hand:"
    echo "    tailscale serve --bg --https=$WEB_HTTPS_PORT http://127.0.0.1:8792"
fi

say "The run on corral's NAS worker (github.com/junglestyle/corral)"
# Hearsay enqueues ideamachine.run at the end of its pipeline, once the stream has new turns. Corral is set up from
# the infra repo; this adds the kind to its worker.
systemctl is-active -q corral-worker && [ -f "$NAS_CORRAL_DIR/worker.toml" ] \
    || die "no corral worker running from $NAS_CORRAL_DIR (journalctl -u corral-worker). It's deployed from the infra
repo; set it up there, then re-run."
grep -q '"nas"' "$NAS_CORRAL_DIR/worker.toml" \
    || die "the corral worker in $NAS_CORRAL_DIR/worker.toml doesn't offer \"nas\"; add it to offers, then re-run"
mkdir -p "$NAS_CORRAL_DIR/kinds.d"
# Blocking flock, so a run started by hand delays the job instead of failing it. systemd-cat keeps its output under
# journalctl -t ideamachine-run.
if write_if_changed "$NAS_CORRAL_DIR/kinds.d/ideamachine.toml" <<EOF
# Written by ideamachine's install/nas.sh on every run.
[kinds."ideamachine.run"]
argv = ["systemd-cat", "-t", "ideamachine-run", "flock", "$RUN_LOCK", $(printf '"%s", ' "${COMPOSE[@]}")"run", "--rm", "-T", "run"]
EOF
then
    systemctl restart corral-worker
    echo "kinds updated, worker restarted"
fi
echo "kinds in $NAS_CORRAL_DIR/kinds.d/ideamachine.toml"
cron_delete "ideamachine run"   # the hourly run at :45, before corral

say "Nightly backups (TrueNAS cron job, output to the journal)"
# RUN_JOB isn't scheduled: it's the run as the worker does it, with the outcome in the journal, for verifying below.
# flock skips a run while one is going. No % in either command: cron would treat it as a newline.
RUN_JOB="( (flock -n -E 75 $RUN_LOCK ${COMPOSE[*]} run --rm -T run </dev/null || echo \"run did not finish: exit \$? (75: skipped, the last run was still going)\") 2>&1 | logger -t ideamachine-run )"
# Your labels, verdicts, stars and pins are the irreplaceable part. Two weeks of nightly dumps, written whole or not
# at all; fast is snapshotted at 4:00 and replicated to storage, so each night's dump is in that night's copy. Only
# im: im_v1, the archived database from before the rebuild, was dumped once at the cutover (docs/deploy.md).
BACKUP_JOB="( (f=$NAS_BACKUPS/im-\$(date -I).dump; ${COMPOSE[*]} exec -T db pg_dump -U postgres -d im -Fc > \$f.part && mv \$f.part \$f && ls -l \$f && find $NAS_BACKUPS -name 'im-*.dump' -mtime +14 -delete || echo \"backup failed: exit \$?\") 2>&1 | logger -t ideamachine-backup )"
cron_ensure "ideamachine backup" 15 3 "$BACKUP_JOB"
echo "logs: journalctl -t ideamachine-run, journalctl -t ideamachine-backup"

say "Verifying"
since="$(date +%s)"
docker exec "$DB_CONTAINER" pg_isready -q -U postgres -d im || die "the database isn't up"
echo "database: up, $(row_counts | awk -F'|' '{n += $2} END {print NR " tables, " n " rows"}')"

# The run and the backup cron job's command, as sh runs them, output to the journal.
flock "$RUN_LOCK" true   # let a run the worker started finish first
sh -c "$RUN_JOB"
sleep 1
run_out="$(journalctl -q -t ideamachine-run --since "@$since" -o cat)"
[ -n "$run_out" ] || die "the run left nothing in the journal (journalctl -t ideamachine-run)"
if grep -q "run did not finish" <<<"$run_out"; then
    tail -n 30 <<<"$run_out"
    die "the run failed (above; all of it: journalctl -t ideamachine-run --since @$since)"
fi
echo "run: ok, reading $HEARSAY_STREAM (journalctl -t ideamachine-run --since @$since)"

sh -c "$BACKUP_JOB"
dump="$NAS_BACKUPS/im-$(date -I).dump"
[ "$(stat -c %Y "$dump" 2>/dev/null || echo 0)" -ge "$since" ] \
    || die "no new dump at $dump (journalctl -t ideamachine-backup --since @$since)"
docker exec -i "$DB_CONTAINER" pg_restore -l < "$dump" >/dev/null || die "$dump isn't a readable dump"
echo "backup: $dump ($(du -h "$dump" | cut -f1), readable)"

# Logged in with its token, the feed reads the database as the web view's role.
[ "$(web_feed http://127.0.0.1:8792/)" = 200 ] \
    || die "the web view's feed didn't load on 127.0.0.1:8792 (journalctl -t ideamachine-web)"
echo "web view: logged in and read the feed on 127.0.0.1:8792"
if [ -n "$ts" ]; then
    # Through tailscale serve on this machine's own tailnet address, as a phone would reach it.
    ts_ip="$(docker exec "$ts" tailscale ip -4 | head -n 1)"
    code="$(web_feed "$WEB_URL" --resolve "$ts_host:$WEB_HTTPS_PORT:$ts_ip")"
    [ "$code" = 200 ] || die "the web view's feed answered $code, not 200, at $WEB_URL"
    echo "web view: the same on the tailnet at $WEB_URL"
fi

say "The im command, for root"
# The system directories are read-only on TrueNAS, so install/bin goes on root's PATH. An upgrade can reset root's
# home; the next run of this script adds the line again.
IM_PATH_LINE="export PATH=\"\$PATH:$REPO_DIR/install/bin\"   # Idea Machine's im (install/nas.sh)"
grep -qxF "$IM_PATH_LINE" /root/.zshrc 2>/dev/null || echo "$IM_PATH_LINE" >> /root/.zshrc

say "Done"
cat <<EOF
Idea Machine runs when Hearsay's pipeline enqueues it on corral (log: journalctl -t ideamachine-run). One run now,
to see it work:
    sudo ${COMPOSE[*]} run --rm run
Any im command, as root (in a new shell, or after: source /root/.zshrc):
    im check
Hearsay's stream: $HEARSAY_STREAM ($HEARSAY_STREAM_WHY)
The web view: $WEB_URL
    token: $(env_get "$NAS_WEB_ENV" IM_WEB_TOKEN)
    (in Safari on the phone: open it, log in, Share -> Add to Home Screen)
EOF
if zfs list -H -o name "$OLD_POOL_DATASET" >/dev/null 2>&1; then
    cat <<EOF

The old copy on storage is still there, unused. Once you've confirmed everything works from fast, remove it (the
old database, dumps, logs and config, and the root-owned clones in it):
    sudo zfs destroy -r $OLD_POOL_DATASET
    sudo git config --global --unset-all safe.directory '^$OLD_ROOT/'
EOF
    for snap in $(zfs list -H -t snapshot -o name -r "$NAS_POOL_DATASET" | grep '@to-fast-' || true); do
        echo "    sudo zfs destroy $snap   # left on fast by the move"
    done
fi
if [ -d /mnt/fast/repos/lattice ]; then
    cat <<EOF

Lattice is no longer deployed (2026-11-11), and nothing here reads its clone. As admin:
    rm -rf /mnt/fast/repos/lattice
Its Cloudflare tunnel and public hostname, and the claude.ai connector, go by hand (docs/deploy.md).
EOF
fi
