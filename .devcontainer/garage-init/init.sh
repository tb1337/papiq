#!/bin/sh
# Prepare a fresh single-node Garage for development: cluster layout, access key, bucket.
# Safe to run repeatedly: every step checks the current state first.
#
# Environment:
#   GARAGE_ADMIN_URL           admin API, e.g. http://garage:3903
#   GARAGE_ADMIN_TOKEN         admin API token
#   PAPIQ_S3_ACCESS_KEY_ID     access key to import (GK + 24 hex digits)
#   PAPIQ_S3_SECRET_ACCESS_KEY its secret (64 hex digits)
#   PAPIQ_S3_BUCKET            bucket to create
set -eu

: "${GARAGE_ADMIN_URL:?}" "${GARAGE_ADMIN_TOKEN:?}"
: "${PAPIQ_S3_ACCESS_KEY_ID:?}" "${PAPIQ_S3_SECRET_ACCESS_KEY:?}" "${PAPIQ_S3_BUCKET:?}"

# api METHOD PATH [JSON_BODY]: prints the response body, fails on HTTP errors.
api() {
  method=$1 path=$2 body=${3:-}
  if [ -n "$body" ]; then
    curl -fsS -X "$method" -H "Authorization: Bearer $GARAGE_ADMIN_TOKEN" \
      -H "Content-Type: application/json" -d "$body" "$GARAGE_ADMIN_URL$path"
  else
    curl -fsS -X "$method" -H "Authorization: Bearer $GARAGE_ADMIN_TOKEN" "$GARAGE_ADMIN_URL$path"
  fi
}

# status METHOD PATH: prints only the HTTP status code.
status() {
  curl -sS -o /dev/null -w '%{http_code}' -X "$1" \
    -H "Authorization: Bearer $GARAGE_ADMIN_TOKEN" "$GARAGE_ADMIN_URL$2"
}

echo "Waiting for Garage at $GARAGE_ADMIN_URL ..."
tries=0
until api GET /v2/GetClusterStatus >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -ge 60 ]; then
    echo "Garage did not become ready in time" >&2
    exit 1
  fi
  sleep 1
done

# Cluster layout: assign the only node a role unless it already has one.
layout=$(api GET /v2/GetClusterLayout)
if [ "$(echo "$layout" | jq '.roles | length')" -eq 0 ]; then
  node_id=$(api GET /v2/GetClusterStatus | jq -r '.nodes[0].id')
  version=$(echo "$layout" | jq '.version + 1')
  echo "Assigning layout (version $version) to node $node_id"
  api POST /v2/UpdateClusterLayout \
    "{\"roles\": [{\"id\": \"$node_id\", \"zone\": \"dev\", \"capacity\": 10737418240, \"tags\": []}]}" \
    >/dev/null
  api POST /v2/ApplyClusterLayout "{\"version\": $version}" >/dev/null
else
  echo "Layout already applied"
fi

# Access key with the fixed development credentials.
if [ "$(status GET "/v2/GetKeyInfo?id=$PAPIQ_S3_ACCESS_KEY_ID")" = "200" ]; then
  echo "Access key already exists"
else
  echo "Importing access key $PAPIQ_S3_ACCESS_KEY_ID"
  api POST /v2/ImportKey \
    "{\"name\": \"papiq-dev\", \"accessKeyId\": \"$PAPIQ_S3_ACCESS_KEY_ID\", \"secretAccessKey\": \"$PAPIQ_S3_SECRET_ACCESS_KEY\"}" \
    >/dev/null
fi

# Bucket.
if [ "$(status GET "/v2/GetBucketInfo?globalAlias=$PAPIQ_S3_BUCKET")" = "200" ]; then
  echo "Bucket already exists"
else
  echo "Creating bucket $PAPIQ_S3_BUCKET"
  api POST /v2/CreateBucket "{\"globalAlias\": \"$PAPIQ_S3_BUCKET\"}" >/dev/null
fi

# Permissions are set, not toggled, so repeating this changes nothing.
bucket_id=$(api GET "/v2/GetBucketInfo?globalAlias=$PAPIQ_S3_BUCKET" | jq -r '.id')
api POST /v2/AllowBucketKey \
  "{\"bucketId\": \"$bucket_id\", \"accessKeyId\": \"$PAPIQ_S3_ACCESS_KEY_ID\", \"permissions\": {\"read\": true, \"write\": true, \"owner\": true}}" \
  >/dev/null

echo "Garage is ready: bucket $PAPIQ_S3_BUCKET, key $PAPIQ_S3_ACCESS_KEY_ID"
