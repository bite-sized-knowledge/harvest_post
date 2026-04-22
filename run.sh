#!/bin/bash
# harvest_post runner invoked on GPU machine after WoL wake.
#
# Responsibilities:
#   1. pull latest code from git (source of truth = origin/prod)
#   2. bring up harvest profile: vllm-chat + harvest-post → drain article_queue
#   3. bring up judge profile: vllm-judge → audit rejected articles
#   4. tear down ALL containers and power off
#
# Safety mechanisms:
#   - trap cleanup EXIT: guaranteed container teardown on any exit path
#   - watchdog timer: forces shutdown after MAX_RUNTIME (90 min)
#   - nuke_port_8000: kills orphan containers before each phase
#   - always shutdown: no SKIP_SHUTDOWN — next cron wakes fresh
#
# All stdout/stderr goes to systemd journal. View via:
#   journalctl -u harvest-post.service -f
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.gpu.yml"
MAC_IP="192.168.219.104"
MAX_RUNTIME=5400  # 90 minutes hard cap

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"; }

# ── Watchdog: background process that forces shutdown after MAX_RUNTIME ──
WATCHDOG_PID=""
start_watchdog() {
    (
        sleep "$MAX_RUNTIME"
        log "WATCHDOG: ${MAX_RUNTIME}s exceeded — forcing shutdown"
        docker compose -f "$COMPOSE_FILE" --profile harvest --profile judge down --timeout 10 2>/dev/null || true
        docker ps -q --filter "publish=8000" | xargs -r docker kill 2>/dev/null || true
        sudo -n /usr/sbin/shutdown -h now
    ) &
    WATCHDOG_PID=$!
}
kill_watchdog() {
    if [ -n "$WATCHDOG_PID" ]; then
        kill "$WATCHDOG_PID" 2>/dev/null || true
        wait "$WATCHDOG_PID" 2>/dev/null || true
        WATCHDOG_PID=""
    fi
}

# ── Trap: guaranteed cleanup on ANY exit (normal, error, signal) ──
cleanup() {
    local exit_code=$?
    log "CLEANUP: tearing down all containers (exit_code=$exit_code)"
    docker compose -f "$COMPOSE_FILE" --profile harvest --profile judge down --timeout 30 2>/dev/null || true
    # Nuclear: kill anything still holding port 8000
    docker ps -q --filter "publish=8000" | xargs -r docker kill 2>/dev/null || true
    kill_watchdog
}
trap cleanup EXIT

# ── Pre-clean: kill orphan containers on port 8000 from previous runs ──
nuke_port_8000() {
    local holders
    holders=$(docker ps -q --filter "publish=8000" 2>/dev/null || true)
    if [ -n "$holders" ]; then
        log "PRE-CLEAN: killing containers on port 8000"
        echo "$holders" | xargs docker kill 2>/dev/null || true
        sleep 2
    fi
}

# ── DB helper: count articles remaining in queue ──
verify_queue_count() {
    python3 - <<'PYEOF'
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

# ═══════════════════════════════════════════════════════════════════════
log "=== Harvest Post Started ==="
start_watchdog

# --- 0. Sanity: can we reach the Mac? ---
if ! ping -c 1 -W 3 "$MAC_IP" > /dev/null 2>&1; then
    log "ERROR: Cannot reach Mac ($MAC_IP). Aborting."
    exit 1
fi

cd "$SCRIPT_DIR"

# --- 1. Pull latest code ---
log "git pull origin prod"
git pull --rebase origin prod || log "WARN: git pull failed, continuing with existing checkout"

# --- 2. Sync secrets from Doppler and normalize for docker compose ---
if [ -f .env.doppler ]; then
    sed -E 's/^([A-Z_]+)="(.*)"$/\1=\2/' .env.doppler \
        | sed 's/^DB_NAME=.*/DB_NAME=bite/' > .env
    if ! grep -q '^QDRANT_ENDPOINT=' .env; then
        QHOST=$(grep '^QDRANT_HOST=' .env | cut -d'=' -f2-)
        QPORT=$(grep '^QDRANT_PORT=' .env | cut -d'=' -f2-)
        echo "QDRANT_ENDPOINT=${QHOST}:${QPORT}" >> .env
    fi
fi

set -a
source .env
set +a

# --- 3. Harvest phase ---
log "=== HARVEST PHASE ==="
nuke_port_8000

log "docker compose --profile harvest up -d --build --wait --wait-timeout 600"
docker compose -f "$COMPOSE_FILE" --profile harvest up -d --build --wait --wait-timeout 600

log "waiting for harvest-post to finish"
set +e
docker compose -f "$COMPOSE_FILE" --profile harvest wait harvest-post
HARVEST_EXIT=$?
set -e
log "harvest-post exited with code $HARVEST_EXIT"

# --- 4. Verify queue is drained, retry up to 2 times ---
MAX_VERIFY=2
for attempt in $(seq 1 $MAX_VERIFY); do
    REMAINING=$(verify_queue_count || echo "")
    if [ -z "$REMAINING" ]; then
        log "VERIFY: DB query failed — skipping retry"
        break
    fi
    if [ "$REMAINING" -eq 0 ]; then
        log "VERIFY: article_queue is empty (attempt $attempt)"
        break
    fi
    log "VERIFY attempt $attempt/$MAX_VERIFY: $REMAINING articles still in queue — re-running harvest-post"
    docker compose -f "$COMPOSE_FILE" --profile harvest up -d harvest-post
    set +e
    docker compose -f "$COMPOSE_FILE" --profile harvest wait harvest-post
    HARVEST_EXIT=$?
    set -e
    log "harvest-post re-run exited with code $HARVEST_EXIT"
done

FINAL_REMAINING=$(verify_queue_count || echo "?")
log "harvest complete: $FINAL_REMAINING articles remaining"

# --- 5. Tear down harvest stack to free VRAM for judge ---
log "docker compose --profile harvest down"
docker compose -f "$COMPOSE_FILE" --profile harvest down --timeout 30

# --- 6. Judge phase: re-evaluate rejected articles ---
set +e
REJECTED_NEW=$(cd "$SCRIPT_DIR/src" && python3 "$SCRIPT_DIR/scripts/review/count_unaudited.py" 2>/dev/null)
COUNT_RC=$?
set -e

if [ "$COUNT_RC" -ne 0 ] || [ -z "$REJECTED_NEW" ]; then
    log "judge phase: count_unaudited failed (rc=$COUNT_RC) — skipping"
elif [ "$REJECTED_NEW" -eq 0 ]; then
    log "judge phase: no unaudited rejected rows — skipping"
else
    log "=== JUDGE PHASE: $REJECTED_NEW unaudited rejected rows ==="
    nuke_port_8000

    set +e
    docker compose -f "$COMPOSE_FILE" --profile judge up -d --wait --wait-timeout 300
    JUDGE_UP_RC=$?
    set -e

    if [ "$JUDGE_UP_RC" -ne 0 ]; then
        log "WARN: judge stack failed to come healthy (rc=$JUDGE_UP_RC) — skipping audit"
        docker compose -f "$COMPOSE_FILE" --profile judge down --timeout 10 || true
    else
        export VLLM_JUDGE_URL="http://localhost:8000/v1"

        set +e
        (cd "$SCRIPT_DIR/src" && python3 "$SCRIPT_DIR/scripts/review/audit_rejected.py" \
            --since 24h --limit 200 --time-budget 15m)
        AUDIT_RC=$?
        (cd "$SCRIPT_DIR/src" && python3 "$SCRIPT_DIR/scripts/review/recover_rejected.py" \
            --limit 200)
        RECOVER_RC=$?
        set -e
        log "judge phase: audit rc=$AUDIT_RC, recover rc=$RECOVER_RC"

        docker compose -f "$COMPOSE_FILE" --profile judge down --timeout 30
    fi
fi

# --- 7. Shutdown (always) ---
# The EXIT trap handles container cleanup if anything is still running.
# Kill the watchdog since we're shutting down cleanly.
kill_watchdog
log "=== Done. Shutting down. ==="
sudo -n /usr/sbin/shutdown -h now
