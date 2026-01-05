#!/usr/bin/env bash

set -euo pipefail

# 이전 dangling 이미지 정리
docker image prune -f --filter "dangling=true" 2>/dev/null || true

# 중지된 lambda-local 컨테이너 정리
docker rm $(docker ps -a -q --filter "ancestor=lambda-local" --filter "status=exited") 2>/dev/null || true

# Qdrant 확인 및 실행 (이미 실행 중이면 건너뜀)
if curl -s http://localhost:6333/health >/dev/null 2>&1; then
  echo "[INFO] Qdrant already running, skipping..."
else
  echo "[INFO] Starting Qdrant container..."
  if docker ps -a --format '{{.Names}}' | grep -q "qdrant"; then
    docker start qdrant
  else
    docker run -d --name qdrant -p 6333:6333 -p 6334:6334 qdrant/qdrant
  fi
  sleep 2
fi

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
  -e QDRANT_HOST=http://host.docker.internal \
  lambda-local
