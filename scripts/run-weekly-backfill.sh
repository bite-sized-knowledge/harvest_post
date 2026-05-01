#!/bin/bash
# Weekly judge backfill — article_rejected의 unaudited 누락분을 한 번에 audit + recover.
# Mac의 wake-and-backfill.sh가 일요일 03:00 KST에 ssh로 호출 (background로).
#
# 정상 사이클 audit는 매 harvest 후 --since 24h --limit 200 cap이라 burst 시
# 누적분이 영영 audit 안 받고 dead-letter로 쌓임. 이 스크립트가 그 누적분 처리.
#
# 호출: 호출측이 미리 systemctl stop harvest-post.service + git pull 까지 완료한 상태여야 함.
# 이 스크립트는 judge profile만 띄우고 audit/recover 돌린 뒤 GPU shutdown 시도.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$SCRIPT_DIR/docker-compose.gpu.yml"
NETWORK="harvest_post_gpu-network"
IMAGE="harvest-post:local"

mkdir -p /home/siroo/logs/audit-backfill
LOG="/home/siroo/logs/audit-backfill/$(date +%Y%m%d_%H%M%S)_weekly.log"

exec >> "$LOG" 2>&1
echo "=== WEEKLY BACKFILL START $(date) ==="
cd "$SCRIPT_DIR"

echo "--- judge profile up ---"
docker compose -f "$COMPOSE" --profile judge up -d --wait --wait-timeout 300

echo "--- audit_rejected.py --backfill --limit 1000 --time-budget 60m ---"
docker run --rm --network "$NETWORK" --env-file .env \
  -e VLLM_JUDGE_URL=http://gpu-vllm-judge:8000/v1 \
  -v "$SCRIPT_DIR/scripts:/app/scripts:ro" \
  "$IMAGE" \
  python /app/scripts/review/audit_rejected.py --backfill --limit 1000 --time-budget 60m
AUDIT_RC=$?
echo "audit rc=$AUDIT_RC"

echo "--- recover_rejected.py --limit 500 ---"
docker run --rm --network "$NETWORK" --env-file .env \
  -v "$SCRIPT_DIR/scripts:/app/scripts:ro" \
  "$IMAGE" \
  python /app/scripts/review/recover_rejected.py --limit 500
RECOVER_RC=$?
echo "recover rc=$RECOVER_RC"

echo "--- judge profile down ---"
docker compose -f "$COMPOSE" --profile judge down --timeout 30

echo "=== WEEKLY BACKFILL DONE $(date) audit=$AUDIT_RC recover=$RECOVER_RC ==="
sudo -n /usr/sbin/shutdown -h +1 || echo "shutdown failed (NOPASSWD missing — manual shutdown 필요)"
