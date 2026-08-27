#!/bin/sh
set -eu

: "${GRAPH_URI:?GRAPH_URI is required}"
: "${ADMIN_NEO4J_USER:?ADMIN_NEO4J_USER is required}"
: "${ADMIN_NEO4J_PASSWORD:?ADMIN_NEO4J_PASSWORD is required}"
: "${APP_NEO4J_USER:?APP_NEO4J_USER is required}"
: "${APP_NEO4J_PASSWORD:?APP_NEO4J_PASSWORD is required}"

case "$APP_NEO4J_USER" in
  *[!A-Za-z0-9_]*) echo "APP_NEO4J_USER contains invalid characters" >&2; exit 1 ;;
esac
if [ "$APP_NEO4J_USER" = "$ADMIN_NEO4J_USER" ]; then
  echo "APP_NEO4J_USER must differ from ADMIN_NEO4J_USER" >&2
  exit 1
fi

# Cypher Shell parameters are Cypher expressions. Escape the two characters
# significant inside a single-quoted Cypher string before constructing the map.
escaped_app_password=$(printf '%s' "$APP_NEO4J_PASSWORD" | sed "s/\\\\/\\\\\\\\/g; s/'/\\\\'/g")

cypher-shell \
  -a "$GRAPH_URI" \
  -u "$ADMIN_NEO4J_USER" \
  -p "$ADMIN_NEO4J_PASSWORD" \
  -d system \
  -P "{app_password: '$escaped_app_password'}" \
  "CREATE OR REPLACE USER \`$APP_NEO4J_USER\`
   SET PASSWORD \$app_password CHANGE NOT REQUIRED;
   GRANT ROLE reader TO \`$APP_NEO4J_USER\`;"

verification=$(cypher-shell \
  -a "$GRAPH_URI" \
  -u "$ADMIN_NEO4J_USER" \
  -p "$ADMIN_NEO4J_PASSWORD" \
  -d system \
  --format plain \
  -P "{app_user: '$APP_NEO4J_USER'}" \
  "SHOW USERS YIELD user, roles
   WHERE user = \$app_user
   RETURN size(roles) = 2
     AND 'PUBLIC' IN roles
     AND 'reader' IN roles AS roles_are_exactly_reader;")

if ! printf '%s\n' "$verification" | grep -qx 'true'; then
  echo "Neo4j application user role verification failed" >&2
  exit 1
fi
