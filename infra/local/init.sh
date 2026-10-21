#!/usr/bin/env bash
set +x
set -euo pipefail
psql -X -U postgres -d team4 -v ON_ERROR_STOP=1 -f /setup/sql/001_service_roles.sql
psql -X -U postgres -d team4 -v ON_ERROR_STOP=1 <<'SQL'
\getenv cs_password LOCAL_CS_PASSWORD
\getenv tft_password LOCAL_TFT_PASSWORD
\getenv platform_password LOCAL_PLATFORM_PASSWORD
ALTER ROLE cs_app PASSWORD :'cs_password';
ALTER ROLE tft_app PASSWORD :'tft_password';
ALTER ROLE platform_app PASSWORD :'platform_password';
SQL
psql -X -U postgres -d team4 -v ON_ERROR_STOP=1 -f /setup/local/extensions.sql
shopt -s nullglob
for service in cs tft platform; do
  for file in /setup/migrations/"$service"/*.sql; do
    psql -X -U postgres -d team4 -v ON_ERROR_STOP=1 -f "$file"
  done
done
psql -X -U postgres -d team4 -v ON_ERROR_STOP=1 -f /setup/local/seed_demo.sql
