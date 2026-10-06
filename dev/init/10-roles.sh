#!/bin/bash
# Runs once, when the dev volume is first created.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U postgres -d im_dev -v pw="$IM_PIPELINE_PASSWORD" <<'SQL'
CREATE ROLE im_pipeline LOGIN PASSWORD :'pw';
SQL
psql -v ON_ERROR_STOP=1 -U postgres -d im_dev -f /docker-entrypoint-initdb.d/bootstrap-db.sql.in
if [ -n "${LATTICE_APP_PASSWORD:-}" ]; then
  psql -v ON_ERROR_STOP=1 -U postgres -d im_dev -v pw="$LATTICE_APP_PASSWORD" <<'SQL'
ALTER ROLE lattice_app LOGIN PASSWORD :'pw';
SQL
fi
