#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)
destination="${1:-./backups}/$stamp"
mkdir -p "$destination"
# Pause writers to capture a consistent database and attachment snapshot.
docker compose stop api bot worker
trap 'docker compose start api bot worker' EXIT INT TERM
docker compose exec -T db pg_dump -U taskmaster -d taskmaster -Fc > "$destination/database.dump"
docker compose run --rm --no-deps --user 0 --entrypoint tar api -czf - -C /data attachments > "$destination/attachments.tar.gz"
cp .env "$destination/server.env"
cp compose.yaml "$destination/compose.yaml"
docker image inspect taskmaster:local --format '{{.Id}}' > "$destination/image-id.txt"
printf 'Backup created: %s\n' "$destination"
