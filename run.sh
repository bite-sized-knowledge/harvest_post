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

# --- 2. Sync secrets from Doppler and force DB_NAME to production ---
cp .env.doppler .env 2>/dev/null || true
sed -i 's/^DB_NAME=.*/DB_NAME=bite/' .env

# --- 3. Bring up the stack (vllm-chat + vllm-embed + harvest-post) ---
# vLLM instances take 2-3 minutes to load the model + KV cache on first
# boot after a machine power cycle. The default depends_on wait tolerance
# is too tight, so we pass --wait-timeout 600 (10 minutes) to give the
# stack plenty of room to come healthy before harvest-post kicks off.
log "docker compose up -d --build --wait --wait-timeout 600"
docker compose -f docker-compose.gpu.yml up -d --build --wait --wait-timeout 600

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
# -n forces non-interactive sudo — required because systemd runs this
# script without a TTY. siroo has NOPASSWD configured for /usr/sbin/shutdown
# so this succeeds without an askpass helper.
sudo -n /usr/sbin/shutdown -h now
