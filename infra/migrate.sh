#!/bin/sh
set -eu
# Initial migrations are explicitly rerunnable; fail immediately on SQL errors.
for migration in /migrations/*.sql; do
    test -f "$migration" || { echo "No migration files found" >&2; exit 1; }
    echo "Applying $(basename "$migration")"
    psql --no-psqlrc --set=ON_ERROR_STOP=1 --file="$migration"
done
