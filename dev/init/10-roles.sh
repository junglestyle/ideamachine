#!/bin/bash
# Runs once, when the database volume is first created (dev compose and the NAS alike).
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U postgres -d "$POSTGRES_DB" -v pw="$IM_PIPELINE_PASSWORD" <<'SQL'
CREATE ROLE im_pipeline LOGIN PASSWORD :'pw';
SQL
psql -v ON_ERROR_STOP=1 -U postgres -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/bootstrap-db.sql.in
if [ -n "${LATTICE_APP_PASSWORD:-}" ]; then
  psql -v ON_ERROR_STOP=1 -U postgres -d "$POSTGRES_DB" -v pw="$LATTICE_APP_PASSWORD" <<'SQL'
ALTER ROLE lattice_app LOGIN PASSWORD :'pw';
SQL
fi
