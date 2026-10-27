#!/usr/bin/env bash
# Local-only PostgreSQL archive commands. Use a disposable project for tests.
set -euo pipefail
umask 077

root="$(cd "$(dirname "$0")/.." && pwd)"
compose=(docker compose -f "$root/compose.yaml")

usage() {
  echo "Usage: $0 backup ARCHIVE.dump | restore ARCHIVE.dump NEW_DATABASE" >&2
  exit 2
}

fail() {
  echo "local PostgreSQL recovery: $*" >&2
  exit 2
}

[[ $# -ge 2 ]] || usage
action="$1"
archive="$2"
[[ -f "$root/compose.yaml" ]] || fail "compose.yaml is missing"

case "$action" in
  backup)
    [[ $# -eq 2 ]] || usage
    [[ ! -e "$archive" && ! -L "$archive" ]] || fail "archive already exists: $archive"
    parent="$(dirname "$archive")"
    mkdir -p -m 700 "$parent"
    [[ ! -L "$parent" ]] || fail "archive directory must not be a symlink"
    temp="$(mktemp "$parent/.runguard-pgdump.XXXXXX")"
    trap 'rm -f "$temp"' EXIT
    "${compose[@]}" exec -T postgres sh -ec \
      'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$temp"
    "${compose[@]}" exec -T postgres pg_restore --list < "$temp" > /dev/null
    ln "$temp" "$archive" || fail "archive already exists: $archive"
    rm "$temp"
    trap - EXIT
    echo "PostgreSQL archive created: $archive"
    ;;
  restore)
    [[ $# -eq 3 ]] || usage
    [[ -f "$archive" && ! -L "$archive" ]] || fail "archive is missing or is a symlink: $archive"
    new_database="$3"
    [[ "$new_database" =~ ^[a-z][a-z0-9_]{0,62}$ ]] || fail "new database name must be a simple lowercase identifier"
    "${compose[@]}" exec -T postgres sh -ec \
      'test "$1" != "$POSTGRES_DB" || { echo "Refusing to restore into the active database" >&2; exit 2; }' sh "$new_database"
    "${compose[@]}" exec -T postgres pg_restore --list < "$archive" > /dev/null
    "${compose[@]}" exec -T postgres sh -ec \
      'createdb -U "$POSTGRES_USER" -T template0 "$1"' sh "$new_database"
    "${compose[@]}" exec -T postgres sh -ec \
      'pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --no-acl --single-transaction --exit-on-error' sh "$new_database" < "$archive"
    echo "PostgreSQL archive restored into fresh database: $new_database"
    ;;
  *)
    usage
    ;;
esac
