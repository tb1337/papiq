#!/bin/sh
# Create the secrets of the example stacks in deploy/secrets/ (not in the repository). Existing
# files are kept, so running it again is harmless. To start over, delete the folder and the
# volumes (`docker compose down -v`): a database or a Garage cluster keeps the old values.
#
# The files are readable by everyone on the host (0644) in a folder only you can enter (0700):
# Docker mounts them into the containers as they are, and the Papiq processes run as PUID:PGID,
# not as the file's owner. Garage insists on 0600 for its own two secrets; it runs as root.
set -eu

dir=$(cd "$(dirname "$0")" && pwd)/secrets
mkdir -p "$dir"
chmod 700 "$dir"

# create NAME VALUE [MODE]: write the value unless the file exists.
create() {
  if [ -e "$dir/$1" ]; then
    echo "kept     $1"
  else
    printf '%s\n' "$2" >"$dir/$1"
    chmod "${3:-644}" "$dir/$1"
    echo "created  $1"
  fi
}

create papiq_secret_key "$(openssl rand -base64 32)"
create admin_password "$(openssl rand -base64 18 | tr '+/' '-_')"
create meilisearch_key "$(openssl rand -hex 32)"
create postgres_password "$(openssl rand -hex 24)"
create garage_rpc_secret "$(openssl rand -hex 32)" 600
create garage_admin_token "$(openssl rand -hex 32)" 600
create s3_access_key_id "GK$(openssl rand -hex 12)"
create s3_secret_access_key "$(openssl rand -hex 32)"
# Meilisearch reads its key from MEILI_MASTER_KEY (no *_FILE variant): an env file with the same key.
if [ ! -e "$dir/meilisearch.env" ]; then
  printf 'MEILI_MASTER_KEY=%s\n' "$(cat "$dir/meilisearch_key")" >"$dir/meilisearch.env"
  chmod 644 "$dir/meilisearch.env"
  echo "created  meilisearch.env"
fi

echo
echo "The admin password (user \"admin\") is in $dir/admin_password."
