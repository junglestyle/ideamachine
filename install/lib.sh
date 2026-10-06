# Shared helpers for install scripts. Source this; don't run it.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# NAS layout (TrueNAS SCALE, pool "storage"), next to Hearsay's and Teem's.
NAS_POOL_DATASET="storage/ideamachine"
NAS_ROOT="/mnt/storage/ideamachine"
NAS_CONFIG="$NAS_ROOT/config"
NAS_PG="$NAS_ROOT/pg"
NAS_LOGS="$NAS_ROOT/logs"
NAS_BACKUPS="$NAS_ROOT/backups"
NAS_LATTICE_REPO="$NAS_ROOT/lattice"
NAS_DB_ENV="$NAS_CONFIG/db.env"
NAS_PIPELINE_ENV="$NAS_CONFIG/pipeline.env"
NAS_LATTICE_ENV="$NAS_CONFIG/lattice.env"
NAS_MOVE_DUMP="$NAS_BACKUPS/from-eeyore.dump"
HEARSAY_STREAM="/mnt/storage/hearsay/stream"
LATTICE_HTTPS_PORT=8444   # Teem has 8443
APPS_UID=568
APPS_GID=568
COMPOSE=(docker compose -f "$REPO_DIR/install/compose.yaml")

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
    [ "$(id -u)" -eq 0 ] || die "run as root: sudo $0"
}

# Env files are plain KEY=VALUE lines, readable by both bash and compose env_file.
env_get() {
    local file="$1" key="$2"
    [ -f "$file" ] || return 0
    sed -n "s/^${key}=//p" "$file" | tail -n 1
}

# Appends KEY only if it's absent, so re-runs never rotate existing values.
env_ensure() {
    local file="$1" key="$2" value="$3"
    [ -n "$(env_get "$file" "$key")" ] && return 0
    [ -n "$value" ] || die "empty value for $key"
    (umask 077; touch "$file")
    printf '%s=%s\n' "$key" "$value" >> "$file"
    chmod 600 "$file"
}

# Creates or updates a TrueNAS cron job (shown in System -> Advanced -> Cron Jobs). The middleware appends its own
# redirect to the command, so the caller's redirect sits inside an outer subshell.
cron_ensure() {
    local description="$1" minute="$2" hour="$3" command="$4" payload id
    payload="$(python3 -c '
import json, sys
print(json.dumps({"user": "root", "command": sys.argv[1], "description": sys.argv[2],
                  "schedule": {"minute": sys.argv[3], "hour": sys.argv[4], "dom": "*", "month": "*", "dow": "*"},
                  "enabled": True, "stdout": True, "stderr": True}))' "$command" "$description" "$minute" "$hour")"
    id="$(midclt call cronjob.query "[[\"description\", \"=\", \"$description\"]]" \
        | python3 -c 'import json, sys; jobs = json.load(sys.stdin); print(jobs[0]["id"] if jobs else "")')"
    if [ -z "$id" ]; then
        midclt call cronjob.create "$payload" >/dev/null
        echo "created: $description"
    else
        midclt call cronjob.update "$id" "$payload" >/dev/null
        echo "up to date: $description (job $id)"
    fi
}

db_sql() {  # runs SQL as the database superuser, prints unaligned tuples
    "${COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d im -tAc "$1"
}

im() {  # runs one `im` command in the pipeline image
    "${COMPOSE[@]}" run --rm -T run "$@" </dev/null
}
