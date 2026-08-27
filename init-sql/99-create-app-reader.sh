#!/bin/sh
set -eu

: "${POSTGRES_APP_USER:?POSTGRES_APP_USER is required}"
: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD is required}"

if [ "$POSTGRES_APP_USER" = "$POSTGRES_USER" ]; then
  echo "POSTGRES_APP_USER must differ from POSTGRES_USER" >&2
  exit 1
fi

set -- \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=app_user="$POSTGRES_APP_USER" \
  --set=app_password="$POSTGRES_APP_PASSWORD"
if [ -n "${POSTGRES_HOST:-}" ]; then
  set -- "$@" --host "$POSTGRES_HOST"
fi

PGPASSWORD="${POSTGRES_PASSWORD:-}" psql "$@" <<'SQL'
BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
\gexec

SELECT format(
  'ALTER ROLE %I NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
  :'app_user'
)
\gexec

SELECT format('REVOKE %I FROM %I', parent.rolname, :'app_user')
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE member.rolname = :'app_user'
\gexec

SELECT format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I', current_database(), :'app_user')
\gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user')
\gexec

SELECT format('ALTER DEFAULT PRIVILEGES GRANT USAGE ON SCHEMAS TO %I', :'app_user')
\gexec

SELECT format('ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO %I', :'app_user')
\gexec

SELECT format('GRANT USAGE ON SCHEMA %I TO %I', nspname, :'app_user')
FROM pg_namespace
WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
\gexec

SELECT format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM %I', nspname, :'app_user')
FROM pg_namespace
WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
\gexec

SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', nspname, :'app_user')
FROM pg_namespace
WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
\gexec

-- 사용자 생성 SQL은 default_transaction_read_only 세션으로 실행하고,
-- 대화기록 저장 전용 세션에만 아래의 최소 INSERT 권한을 사용한다.
SELECT format('GRANT INSERT ON TABLE app.conversation_history TO %I', :'app_user')
WHERE to_regclass('app.conversation_history') IS NOT NULL
\gexec

SELECT format(
  'GRANT USAGE, SELECT ON SEQUENCE app.conversation_history_id_seq TO %I',
  :'app_user'
)
WHERE to_regclass('app.conversation_history_id_seq') IS NOT NULL
\gexec

SELECT format(
  'ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT ON TABLES TO %I',
  nspname,
  :'app_user'
)
FROM pg_namespace
WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
\gexec

SELECT format('REVOKE CREATE ON SCHEMA %I FROM %I', nspname, :'app_user')
FROM pg_namespace
WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
\gexec

SELECT format('REVOKE CREATE ON SCHEMA %I FROM PUBLIC', nspname)
FROM pg_namespace
WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
\gexec

SELECT format('REVOKE CREATE ON DATABASE %I FROM %I', current_database(), :'app_user')
\gexec

SELECT format('REVOKE CREATE ON DATABASE %I FROM PUBLIC', current_database())
\gexec

SELECT format('REVOKE TEMPORARY ON DATABASE %I FROM %I', current_database(), :'app_user')
\gexec

SELECT format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database())
\gexec

-- Division by zero aborts and rolls back provisioning if the role is not
-- demonstrably read-only after repairing an existing installation.
SELECT 1 / CASE WHEN
  NOT rolinherit
  AND NOT rolsuper
  AND NOT rolcreaterole
  AND NOT rolcreatedb
  AND NOT rolreplication
  AND NOT rolbypassrls
  AND NOT EXISTS (
    SELECT 1
    FROM pg_auth_members membership
    WHERE membership.member = app_role.oid
  )
  AND NOT EXISTS (
    SELECT 1
    FROM pg_class relation
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE relation.relowner = app_role.oid
      AND namespace.nspname NOT LIKE 'pg_%'
      AND namespace.nspname <> 'information_schema'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM pg_namespace namespace
    WHERE namespace.nspowner = app_role.oid
      AND namespace.nspname NOT LIKE 'pg_%'
      AND namespace.nspname <> 'information_schema'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM pg_database database
    WHERE database.datname = current_database()
      AND database.datdba = app_role.oid
  )
  AND NOT has_database_privilege(
    app_role.rolname,
    current_database(),
    'TEMPORARY'
  )
  THEN 1 ELSE 0 END AS application_role_is_read_only
FROM pg_roles app_role
WHERE rolname = :'app_user';

COMMIT;
SQL
