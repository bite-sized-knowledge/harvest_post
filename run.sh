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

# --- 2. Sync secrets from Doppler and normalize for docker compose ---
# - Strip quotes from values (Doppler outputs quoted strings; some docker
#   compose versions pass them through verbatim, breaking DB auth)
# - Force DB_NAME=bite (production)
# - Derive QDRANT_ENDPOINT for prod-mode Python code path
if [ -f .env.doppler ]; then
    sed -E 's/^([A-Z_]+)="(.*)"$/\1=\2/' .env.doppler \
        | sed 's/^DB_NAME=.*/DB_NAME=bite/' > .env
    # QDRANT_ENDPOINT = QDRANT_HOST:QDRANT_PORT (required when ENVIRONMENT=prod)
    if ! grep -q '^QDRANT_ENDPOINT=' .env; then
        QHOST=$(grep '^QDRANT_HOST=' .env | cut -d'=' -f2-)
        QPORT=$(grep '^QDRANT_PORT=' .env | cut -d'=' -f2-)
        echo "QDRANT_ENDPOINT=${QHOST}:${QPORT}" >> .env
    fi
fi

# Export .env so child processes (sync scripts) use fresh credentials
# instead of potentially stale systemd Environment= values.
set -a
source .env
set +a

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

# --- 5. Verification: confirm article_queue is actually drained ---
# harvest-post may exit early (crash, LLM error, network hiccup) while
# articles are still in the queue. Before we power down the GPU, verify
# against the production DB and re-run the container up to MAX_VERIFY
# times if anything remains. If the queue is still non-empty after the
# retries, skip shutdown so the next 3-hour cron can pick up where we
# left off (and so the situation is visible instead of silently lost).
verify_queue_count() {
    python3 - <<'PYEOF' 2>/dev/null
import os, ssl, sys
import pymysql
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
try:
    conn = pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "bite"),
        ssl=ctx,
        connect_timeout=10,
    )
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM article_queue")
    print(cur.fetchone()[0])
    conn.close()
except Exception as e:
    print(f"ERR:{e}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

MAX_VERIFY=2
SKIP_SHUTDOWN=0
for attempt in $(seq 1 $MAX_VERIFY); do
    REMAINING=$(verify_queue_count || echo "")
    if [ -z "$REMAINING" ]; then
        log "VERIFY: DB query failed — proceeding to shutdown without retry"
        break
    fi
    if [ "$REMAINING" -eq 0 ]; then
        log "VERIFY: article_queue is empty (attempt $attempt)"
        break
    fi
    log "VERIFY attempt $attempt/$MAX_VERIFY: $REMAINING articles still in queue — re-running harvest-post"
    docker compose -f docker-compose.gpu.yml up -d harvest-post
    set +e
    docker compose -f docker-compose.gpu.yml wait harvest-post
    HARVEST_EXIT=$?
    set -e
    log "harvest-post re-run exited with code $HARVEST_EXIT"
done

FINAL_REMAINING=$(verify_queue_count || echo "")
if [ -n "$FINAL_REMAINING" ] && [ "$FINAL_REMAINING" -gt 0 ]; then
    log "WARN: $FINAL_REMAINING articles remain after $MAX_VERIFY retries — SKIPPING shutdown (leaving GPU on for next cron to investigate)"
    SKIP_SHUTDOWN=1
fi

# --- 6. Sync bite → bite_dev (best-effort, do not fail overall run) ---
if [ -f "$SCRIPT_DIR/scripts/sync_bite_to_dev.py" ]; then
    log "syncing bite → bite_dev"
    python3 "$SCRIPT_DIR/scripts/sync_bite_to_dev.py" || log "WARN: dev sync failed"
else
    log "sync script not found, skipping dev sync"
fi

# --- 7. Tear down + power off (if verified clean) ---
log "docker compose down"
docker compose -f docker-compose.gpu.yml down

if [ "$SKIP_SHUTDOWN" -eq 1 ]; then
    log "=== Done. GPU LEFT ON for investigation (queue not drained). ==="
    exit 0
fi

log "=== Done. Shutting down. ==="
# -n forces non-interactive sudo — required because systemd runs this
# script without a TTY. siroo has NOPASSWD configured for /usr/sbin/shutdown
# so this succeeds without an askpass helper.
sudo -n /usr/sbin/shutdown -h now
