#!/bin/bash
# harvest_post runner invoked on GPU machine after WoL wake.
#
# Responsibilities:
#   1. pull latest code from git (source of truth = origin/prod)
#   2. bring up docker-compose stack: vllm-chat + vllm-embed + harvest-post
#   3. wait for harvest-post container to finish draining article_queue
#   4. sync bite → bite_dev
#   5. tear down and power off
#
# All stdout/stderr goes to systemd journal (no /var/log files). View via:
#   journalctl -u harvest-post.service -f
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MAC_IP="192.168.219.104"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"; }

log "=== Harvest Post Started ==="

# --- 0. Sanity: can we reach the Mac? ---
if ! ping -c 1 -W 3 "$MAC_IP" > /dev/null 2>&1; then
    log "ERROR: Cannot reach Mac ($MAC_IP). Aborting."
    exit 1
fi

cd "$SCRIPT_DIR"

# --- 1. Pull latest code ---
# Deploy webhook may have already done this, but re-running is idempotent
# and covers the cron-triggered wake path where no webhook ran.
log "git pull origin prod"
git pull --rebase origin prod || log "WARN: git pull failed, continuing with existing checkout"

# --- 2. Force DB_NAME to production regardless of stored .env value ---
sed -i 's/^DB_NAME=.*/DB_NAME=bite/' .env

# --- 3. Bring up the stack (vllm-chat + vllm-embed + harvest-post) ---
# docker-compose.gpu.yml's harvest-post has depends_on:{vllm-chat,vllm-embed}
# with service_healthy conditions, so `up -d` blocks until vLLM instances
# report healthy before starting harvest-post.
log "docker compose up -d --build"
docker compose -f docker-compose.gpu.yml up -d --build

# --- 4. Wait for harvest-post container to exit ---
# harvest-post CMD is `python3 -m main --continuous`, which exits after the
# queue is empty. docker compose wait blocks until the container stops and
# returns its exit code.
log "waiting for harvest-post to finish"
set +e
docker compose -f docker-compose.gpu.yml wait harvest-post
HARVEST_EXIT=$?
set -e
log "harvest-post exited with code $HARVEST_EXIT"

# --- 5. Sync bite → bite_dev (best-effort, do not fail overall run) ---
if [ -f "$SCRIPT_DIR/scripts/sync_bite_to_dev.py" ]; then
    log "syncing bite → bite_dev"
    python3 "$SCRIPT_DIR/scripts/sync_bite_to_dev.py" || log "WARN: dev sync failed"
else
    log "sync script not found, skipping dev sync"
fi

# --- 6. Tear down + power off ---
log "docker compose down"
docker compose -f docker-compose.gpu.yml down

log "=== Done. Shutting down. ==="
sudo /usr/sbin/shutdown -h now
