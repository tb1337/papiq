#!/usr/bin/env bash
# Test a built Papiq image with the docker CLI only (CI and local):
#
#   docker build --target runtime -t papiq:local .
#   deploy/test-image.sh papiq:local
#
# Starts the image with SQLite and the filesystem and checks: configuration from secret files,
# migration, health, the user of the Papiq processes (PUID/PGID, never root), PAPIQ_ROLE, a PDF
# through OCR and Docling up to its lane (no language model: classification is uncertain, so
# the lane is yellow), invalid configuration and a clean `docker stop`. Needs docker, curl and
# python3 on the host; takes a few minutes. No language model, no other service.
set -euo pipefail

image=${1:?usage: test-image.sh IMAGE}
here=$(cd "$(dirname "$0")" && pwd)
sample=$here/../backend/tests/samples/text.pdf
prefix=papiq-test-$$
work=$(mktemp -d)
chmod 755 "$work"
uid=1234
gid=4321
failures=0

cleanup() {
  docker rm -fv $(docker ps -aq --filter "name=^${prefix}-") >/dev/null 2>&1 || true
  docker volume rm $(docker volume ls -q --filter "name=^${prefix}-") >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }
ok() { printf '   ok: %s\n' "$*"; }
fail() {
  printf '   FAILED: %s\n' "$*" >&2
  failures=$((failures + 1))
}
check() { # check DESCRIPTION COMMAND...
  local description=$1
  shift
  if "$@" >/dev/null 2>&1; then ok "$description"; else fail "$description"; fi
}
die() {
  printf '\nFATAL: %s\n' "$*" >&2
  exit 1
}

# Secrets as files, like Docker secrets: generated here, never part of the repository.
openssl rand -base64 32 >"$work/secret_key"
openssl rand -hex 16 >"$work/admin_password"
chmod 644 "$work"/secret_key "$work"/admin_password

# start NAME [ENV=VALUE...]: a container with SQLite on its own volume (VOLUME=name to share one).
start() {
  local name=$prefix-$1 volume=${VOLUME:-$prefix-$1}
  shift
  local args=()
  for variable in "$@"; do args+=(-e "$variable"); done
  docker run -d --name "$name" \
    -e PUID=$uid -e PGID=$gid \
    -e PAPIQ_SECRET_KEY_FILE=/run/secrets/secret_key \
    -e PAPIQ_ADMIN_USERNAME=admin -e PAPIQ_ADMIN_PASSWORD_FILE=/run/secrets/admin_password \
    -e PAPIQ_COOKIE_SECURE=false \
    -v "$work/secret_key:/run/secrets/secret_key:ro" \
    -v "$work/admin_password:/run/secrets/admin_password:ro" \
    -v "$volume:/data" \
    -p 127.0.0.1::8000 \
    ${args[@]+"${args[@]}"} "$image" >/dev/null
}

status() { docker inspect --format '{{.State.Health.Status}}' "$prefix-$1"; }

wait_for() { # wait_for SECONDS DESCRIPTION COMMAND...
  local seconds=$1 description=$2
  shift 2
  for _ in $(seq "$seconds"); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  fail "$description (waited ${seconds}s)"
  return 1
}

healthy() { [ "$(status "$1")" = healthy ]; }
exited() { [ "$(docker inspect --format '{{.State.Status}}' "$prefix-$1")" = exited ]; }
logs() { docker logs "$prefix-$1" 2>&1; }
url() { echo "http://$(docker port "$prefix-$1" 8000/tcp | head -1)/api/v1"; }

# Uid:gid and command line of every process whose command line contains $2.
processes() { # processes NAME PATTERN
  docker exec "$prefix-$1" sh -c '
    for p in /proc/[0-9]*; do
      cmd=$(tr "\0" " " <"$p/cmdline" 2>/dev/null) || continue
      case "$cmd" in *"$1"*) echo "$(awk "/^Uid/ {print \$2}" "$p/status"):$(awk "/^Gid/ {print \$2}" "$p/status") $cmd" ;; esac
    done' sh "$2" | grep -aE '^[0-9]+:[0-9]+ ' | grep -av 'sh -c' || true
}
count() { processes "$@" | wc -l | tr -d ' '; }

step "image: no secret in the build"
if docker image inspect --format '{{json .Config.Env}}' "$image" | grep -Eqi 'secret|password|token'; then
  fail "the image environment contains a secret-looking variable"
else
  ok "the image environment has no secret"
fi
check "the image runs s6-overlay as PID 1" \
  test "$(docker image inspect --format '{{json .Config.Entrypoint}}' "$image")" = '["/init"]'

step "role all: SQLite, configuration from secret files, PUID=$uid PGID=$gid"
start all
wait_for 180 "the container becomes healthy" healthy all || {
  logs all
  die "the container did not become healthy"
}
ok "healthy"
check "init-migrations migrated the schema" grep -q 'database migrated' <(logs all)
check "the API answers /health with ok" \
  bash -c "curl -fsS $(url all)/health | grep -q '\"status\":\"ok\"'"
check "API and worker run (two processes)" test "$(count all papiq.composition)" -eq 2
check "every Papiq process runs as $uid:$gid" \
  test "$(processes all papiq.composition | grep -vc "^$uid:$gid " || true)" -eq 0
check "no Papiq process runs as root" \
  test "$(processes all papiq.composition | grep -c '^0:' || true)" -eq 0
check "/data belongs to $uid:$gid" \
  docker exec "$prefix-all" sh -c "test \"\$(stat -c %u:%g /data /data/papiq.db /data/objects | sort -u)\" = $uid:$gid"
check "the secret key does not show in the logs" \
  test "$(logs all | grep -cF -f "$work/secret_key")" -eq 0
check "the admin password does not show in the logs" \
  test "$(logs all | grep -cF -f "$work/admin_password")" -eq 0
check "\`papiq\` runs the commands of the composition root as the same user" \
  docker exec "$prefix-all" papiq check-schema

step "a PDF through OCR and Docling up to its lane"
jar=$work/cookies
token=$(curl -fsS -c "$jar" -H 'Content-Type: application/json' \
  -d "{\"username\": \"admin\", \"password\": \"$(cat "$work/admin_password")\"}" \
  "$(url all)/auth/login" | python3 -c 'import json, sys; print(json.load(sys.stdin)["csrf_token"])')
id=$(curl -fsS -b "$jar" -H "X-CSRF-Token: $token" -F "file=@$sample" "$(url all)/documents" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
document() { curl -fsS -b "$jar" "$(url all)/documents/$id"; }
lane() { document | python3 -c 'import json, sys; print(json.load(sys.stdin).get("lane") or "")'; }
has_lane() { [ -n "$(lane)" ]; }
if wait_for 300 "the document reaches a lane" has_lane; then
  outcomes=$(document | python3 -c 'import json, sys; print(json.dumps(json.load(sys.stdin)["processing"]["outcomes"]))')
  echo "   lane: $(lane), outcomes: $outcomes"
  check "lane yellow (no language model: classification is uncertain)" test "$(lane)" = yellow
  check "OCR and parsing succeeded" python3 -c "
import json, sys
outcomes = json.loads(sys.argv[1])
sys.exit(0 if outcomes.get('ocr') == 'ok' and outcomes.get('parse') == 'ok' else 1)" "$outcomes"
fi

step "docker stop: orderly, no kill"
started=$SECONDS
docker stop "$prefix-all" >/dev/null
elapsed=$((SECONDS - started))
check "exit code 0 (not 137)" test "$(docker inspect --format '{{.State.ExitCode}}' "$prefix-all")" -eq 0
check "stopped in ${elapsed}s (under 20 s when idle)" test "$elapsed" -lt 20
check "the worker and Uvicorn logged their shutdown" \
  bash -c "docker logs $prefix-all 2>&1 | grep -q 'worker stopped' && docker logs $prefix-all 2>&1 | grep -q 'Finished server process'"

step "role api: no worker"
start api PAPIQ_ROLE=api
wait_for 180 "healthy" healthy api || logs api
check "the API runs" test "$(count api 'composition api')" -eq 1
check "no worker process" test "$(count api 'composition worker')" -eq 0
check "svc-worker stays down" bash -c "docker exec $prefix-api s6-svstat /run/service/svc-worker | grep -q '^down'"

step "role worker: waits for the schema, starts when the API container has migrated"
start waiter PAPIQ_ROLE=worker
wait_for 60 "a worker waits for the schema" bash -c "docker logs $prefix-waiter 2>&1 | grep -q 'waiting for the database schema'"
started=$SECONDS
docker stop "$prefix-waiter" >/dev/null
check "a waiting worker stops at once, exit code 0 (took $((SECONDS - started)) s)" \
  bash -c "[ $((SECONDS - started)) -lt 20 ] && [ \"\$(docker inspect --format '{{.State.ExitCode}}' $prefix-waiter)\" = 0 ]"
VOLUME=$prefix-shared start worker PAPIQ_ROLE=worker
wait_for 60 "the worker waits for the schema" bash -c "docker logs $prefix-worker 2>&1 | grep -q 'waiting for the database schema'"
check "no migration by the worker" test "$(logs worker | grep -c 'database migrated')" -eq 0
check "not healthy yet" bash -c "[ \"\$(docker inspect --format '{{.State.Health.Status}}' $prefix-worker)\" != healthy ]"
VOLUME=$prefix-shared start migrator PAPIQ_ROLE=api
wait_for 180 "the worker container becomes healthy" healthy worker || logs worker
check "the worker runs" test "$(count worker 'composition worker')" -eq 1
check "the worker is ready for the healthcheck" docker exec "$prefix-worker" test -e /run/papiq-worker-ready
check "no API process in the worker container" test "$(count worker 'composition api')" -eq 0

step "invalid configuration stops the container with a reason"
docker run -d --name "$prefix-nokey" -e PUID=$uid -e PGID=$gid "$image" >/dev/null
wait_for 60 "the container stops" exited nokey || true
check "exit code is not 0" test "$(docker inspect --format '{{.State.ExitCode}}' "$prefix-nokey")" -ne 0
check "the log names PAPIQ_SECRET_KEY" grep -q 'PAPIQ_SECRET_KEY is required' <(logs nokey)
docker run -d --name "$prefix-root" -e PUID=0 -e PAPIQ_SECRET_KEY_FILE=/run/secrets/secret_key \
  -v "$work/secret_key:/run/secrets/secret_key:ro" "$image" >/dev/null
wait_for 60 "the container with PUID=0 stops" exited root || true
check "PUID=0 is refused" grep -q 'must not be 0' <(logs root)
docker run -d --name "$prefix-path" -e PAPIQ_DB_SQLITE_PATH=/papiq.db -e PAPIQ_SECRET_KEY_FILE=/run/secrets/secret_key \
  -v "$work/secret_key:/run/secrets/secret_key:ro" "$image" >/dev/null
wait_for 60 "the container with a database in / stops" exited path || true
check "a database path in the root folder is refused" grep -q 'is the root folder' <(logs path)
docker run -d --name "$prefix-role" -e PAPIQ_ROLE=proxy -e PAPIQ_SECRET_KEY_FILE=/run/secrets/secret_key \
  -v "$work/secret_key:/run/secrets/secret_key:ro" "$image" >/dev/null
wait_for 60 "the container with an invalid role stops" exited role || true
check "an invalid PAPIQ_ROLE is refused" grep -q 'PAPIQ_ROLE' <(logs role)

printf '\n'
if [ "$failures" -gt 0 ]; then
  echo "$failures check(s) failed" >&2
  exit 1
fi
echo "All checks passed."
