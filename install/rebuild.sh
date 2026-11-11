#!/usr/bin/env bash
# The rebuild database (decision 0012): creates im_rebuild beside im in the NAS's Postgres, sets it up as the live one
# is (dev/init/bootstrap-db.sql.in, the names role's grants), writes its connections and its config (rebuild.toml,
# only if missing), and migrates it. install/bin/im-rebuild runs Idea Machine against it. The live database isn't
# touched. Run as root, after install/nas.sh, which re-runs this on every update while im_rebuild exists. Safe to
# re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

NAMES_ENV="$NAS_ROOT/gpu/names.env"
NAMES_REBUILD_ENV="$NAS_ROOT/gpu/names-rebuild.env"

rebuild_url() {  # a live connection URL, $1, from file $2, with the rebuild database in place of im
    case "$1" in
        */im) printf '%s/%s\n' "${1%/im}" "$REBUILD_DB" ;;
        *) die "IM_DATABASE_URL in $2 doesn't end in /im; can't derive the rebuild's from it" ;;
    esac
}

require_root

say "Checking the live install"
docker exec "$DB_CONTAINER" pg_isready -q -U postgres -d im 2>/dev/null \
    || die "the database isn't up. Run install/nas.sh first."
[ -f "$NAMES_ENV" ] || die "no $NAMES_ENV (the names role). Run install/nas.sh first."
pipeline_url="$(rebuild_url "$(env_get "$NAS_PIPELINE_ENV" IM_DATABASE_URL)" "$NAS_PIPELINE_ENV")"
names_url="$(rebuild_url "$(env_get "$NAMES_ENV" IM_DATABASE_URL)" "$NAMES_ENV")"
echo "ok"

say "Database $REBUILD_DB"
if rebuild_exists; then
    echo "already there"
else
    db_sql "CREATE DATABASE $REBUILD_DB" >/dev/null
    echo "created"
fi
# The same per-database setup as im's: extension vector, schemas im and pub owned by im_pipeline, lattice_app, jit off.
"${COMPOSE[@]}" exec -T db psql -q -v ON_ERROR_STOP=1 -U postgres -d "$REBUILD_DB" \
    -f /docker-entrypoint-initdb.d/bootstrap-db.sql.in

say "Connections"
# Derived from the live ones on every run, so no secret is kept twice by hand and a new password carries over. Only
# the URL: everything else (the API keys, IM_NAME_KEY) comes from pipeline.env, which the run service still loads.
(umask 077; printf 'IM_DATABASE_URL=%s\n' "$pipeline_url" > "$NAS_REBUILD_ENV")
chmod 600 "$NAS_REBUILD_ENV"
echo "$NAS_REBUILD_ENV"
# For the GPU worker's names job (install/gpu.sh), like names.env: readable by `ssh nas` (the clones' owner) only.
CLONE_OWNER="$(stat -c %U "$REPO_DIR")"
printf 'IM_DATABASE_URL=%s\n' "$names_url" | install -o "$CLONE_OWNER" -m 600 /dev/stdin "$NAMES_REBUILD_ENV"
echo "$NAMES_REBUILD_ENV"

say "Config ($NAS_CONFIG/im/rebuild.toml)"
if [ ! -f "$NAS_CONFIG/im/rebuild.toml" ]; then
    cat > "$NAS_CONFIG/im/rebuild.toml" <<'TOML'
# The rebuild's config (decision 0012): im-rebuild reads it in place of im.toml, against database im_rebuild. Every
# key and its default is in src/im/config.py. Written once by install/rebuild.sh, never overwritten: edit it here.
# Decision 0012 retires extract.interests, so it isn't set here.
[extract]
prompt = "v5.3"             # extraction v5, its latest version
model = "claude-opus-5-5"   # the reference extractor
effort = "medium"           # how hard it thinks; part of extraction's version, like the model
monthly_cap_usd = 100       # this database's own cap: it counts what its own egress log says was spent this month

[lattice]
enabled = false             # bulk extraction first; set to true to start matching captures into clusters

[match]
# model = "claude-opus-5-5"   # the matcher, once [lattice] is on
# effort = "low"

[themes]
# model = "claude-opus-5-5"   # describing, filing and proposing themes
# effort = "low"

[digest]
# model = "claude-opus-5-5"   # the weekly digest
# effort = "low"
TOML
    echo "created"
else
    echo "already there; left as it is"
fi
chown "root:$APPS_GID" "$NAS_CONFIG/im/rebuild.toml"; chmod 640 "$NAS_CONFIG/im/rebuild.toml"

say "Migrations"
rebuild_env
"${COMPOSE[@]}" run --rm -T "${REBUILD_RUN[@]}" run migrate </dev/null

say "The names role's grants in $REBUILD_DB"
grant_names "$REBUILD_DB"
echo "ok"

say "Done"
cat <<EOF
Run Idea Machine against $REBUILD_DB as root: im-rebuild pending, im-rebuild run (install/bin/im-rebuild; the same
arguments as im). Its config: $NAS_CONFIG/im/rebuild.toml. For names on the dev box's GPU, re-run install/gpu.sh
there, then: corral enqueue ideamachine.names-rebuild --needs gpu --key ideamachine.names-rebuild
EOF
