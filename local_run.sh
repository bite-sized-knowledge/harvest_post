#!/usr/bin/env bash

set -euo pipefail

docker build --platform linux/x86_64 -t lambda-local .

TMP_ENV_FILE=$(mktemp)
trap 'rm -f "$TMP_ENV_FILE"' EXIT

doppler secrets download \
  --project harvest_post \
  --config dev \
  --format env \
  --no-file | sed 's/"//g' > "$TMP_ENV_FILE"

docker run -p 9000:8080 \
  --env-file "$TMP_ENV_FILE" \
  lambda-local
