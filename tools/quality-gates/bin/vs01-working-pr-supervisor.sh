#!/bin/bash -p
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
set -euo pipefail
umask 077

# One functional gate: Java webhook -> immutable manifest and coverage -> Redis
# -> production Python orchestration with offline boundaries -> PostgreSQL
# analysis/outbox -> one provider delivery.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
IMAGE_MANIFEST="$REPOSITORY_ROOT/tools/offline-harness/requirements/persistence-images-v1.json"
IMAGE_VALIDATOR="$REPOSITORY_ROOT/tools/offline-harness/bin/validate-persistence-images.py"
WORKER="$REPOSITORY_ROOT/python-ecosystem/inference-orchestrator/tests/support/vs01_pr_analysis_worker.py"
JAVA_TEST="$REPOSITORY_ROOT/java-ecosystem/services/pipeline-agent/src/test/java/org/rostilos/codecrow/pipelineagent/WorkingPrAnalysisFlowTest.java"
PYTHON_BIN="${VS01_PYTHON_BIN:-$REPOSITORY_ROOT/.venv/bin/python3}"
JAVA_TEST_SELECTOR="WorkingPrAnalysisFlowTest#oneExactPullRequestPersistsCompleteCoverageAndDeliversOneFinding"
WORKER_JOB_TIMEOUT_SECONDS=180

if [[ $# -ne 0 ]]; then
  echo "usage: vs01-working-pr-supervisor.sh" >&2
  exit 64
fi
for required in "$IMAGE_MANIFEST" "$IMAGE_VALIDATOR" "$WORKER" "$JAVA_TEST"; do
  if [[ ! -f "$required" || -L "$required" ]]; then
    echo "ERROR: working-PR prerequisite is missing or symlinked: $required" >&2
    exit 66
  fi
done
for tool in /usr/bin/docker /usr/bin/python3 /usr/bin/mvn "$PYTHON_BIN"; do
  if [[ ! -x "$tool" ]]; then
    echo "ERROR: working-PR gate requires $tool" >&2
    exit 69
  fi
done
if ! "$PYTHON_BIN" -c 'import redis.asyncio, pydantic' >/dev/null 2>&1; then
  echo "ERROR: Python environment lacks redis.asyncio or pydantic: $PYTHON_BIN" >&2
  exit 69
fi

IMAGE_REFERENCES="$(
  /usr/bin/python3 "$IMAGE_VALIDATOR" \
    --print-runtime-references "$IMAGE_MANIFEST"
)"
POSTGRES_IMAGE=""
REDIS_IMAGE=""
POSTGRES_IMAGE_COUNT=0
REDIS_IMAGE_COUNT=0
while IFS= read -r image_reference; do
  case "$image_reference" in
    postgres@sha256:*)
      POSTGRES_IMAGE="$image_reference"
      POSTGRES_IMAGE_COUNT=$((POSTGRES_IMAGE_COUNT + 1))
      ;;
    redis@sha256:*)
      REDIS_IMAGE="$image_reference"
      REDIS_IMAGE_COUNT=$((REDIS_IMAGE_COUNT + 1))
      ;;
  esac
done <<<"$IMAGE_REFERENCES"
if [[ "$POSTGRES_IMAGE_COUNT" -ne 1 || "$REDIS_IMAGE_COUNT" -ne 1 ]]; then
  echo "ERROR: persistence manifest must pin one Postgres and one Redis image" >&2
  exit 65
fi
/usr/bin/docker image inspect "$POSTGRES_IMAGE" "$REDIS_IMAGE" >/dev/null

RUN_TOKEN="$(tr -d '-' </proc/sys/kernel/random/uuid)"
RUN_ID="working-pr-${RUN_TOKEN:0:24}"
ARTIFACT_PARENT="$REPOSITORY_ROOT/.llm-handoff-artifacts/working-pr"
ARTIFACT_ROOT="$ARTIFACT_PARENT/$RUN_ID"
mkdir -p "$ARTIFACT_ROOT"
chmod 0700 "$ARTIFACT_PARENT" "$ARTIFACT_ROOT"
ARTIFACT_ROOT="$(realpath -e "$ARTIFACT_ROOT")"

POSTGRES_ID=""
REDIS_ID=""
WORKER_PID=""
JAVA_PID=""
RUN_COMPLETE=0
SUMMARY="$ARTIFACT_ROOT/summary.txt"
WORKER_STDOUT="$ARTIFACT_ROOT/python-worker.jsonl"
WORKER_STDERR="$ARTIFACT_ROOT/python-worker.log"
WORKER_LEDGER="$ARTIFACT_ROOT/python-worker-ledger.json"
JAVA_LOG="$ARTIFACT_ROOT/java-test.log"

cleanup_container() {
  local container_id="$1"
  local log_path="$2"
  local owned_run=""
  if [[ ! "$container_id" =~ ^[0-9a-f]{64}$ ]]; then
    return
  fi
  if ! /usr/bin/docker container inspect "$container_id" >/dev/null 2>&1; then
    return
  fi
  /usr/bin/docker logs "$container_id" >"$log_path" 2>&1 || true
  owned_run="$(
    /usr/bin/docker inspect \
      --format '{{ index .Config.Labels "codecrow.working-pr.run" }}' \
      "$container_id" 2>/dev/null || true
  )"
  if [[ "$owned_run" == "$RUN_ID" ]]; then
    /usr/bin/docker rm --force "$container_id" >/dev/null 2>&1 || true
  else
    printf 'cleanup_error=container %s lost ownership label\n' \
      "$container_id" >>"$SUMMARY"
  fi
}

cleanup() {
  local status=$?
  set +e
  if [[ -n "$WORKER_PID" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill -TERM "$WORKER_PID" 2>/dev/null
    wait "$WORKER_PID" 2>/dev/null
  fi
  if [[ -n "$JAVA_PID" ]] && kill -0 "$JAVA_PID" 2>/dev/null; then
    kill -TERM "$JAVA_PID" 2>/dev/null
    wait "$JAVA_PID" 2>/dev/null
  fi
  cleanup_container "$POSTGRES_ID" "$ARTIFACT_ROOT/postgres.log"
  cleanup_container "$REDIS_ID" "$ARTIFACT_ROOT/redis.log"
  if [[ "$RUN_COMPLETE" -ne 1 ]]; then
    printf 'status=FAIL\nexit_code=%s\n' "$status" >>"$SUMMARY"
  fi
  echo "Working-PR artifacts: $ARTIFACT_ROOT" >&2
  return "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'status=RUNNING\nrun_id=%s\njava_selector=%s\n' \
  "$RUN_ID" "$JAVA_TEST_SELECTOR" >"$SUMMARY"

POSTGRES_ID="$(
  /usr/bin/docker run --detach --pull never --platform linux/amd64 \
    --label "codecrow.working-pr.run=$RUN_ID" \
    --cap-drop ALL --security-opt no-new-privileges --read-only \
    --publish 127.0.0.1::5432 \
    --user postgres:postgres \
    --env POSTGRES_DB=working_pr \
    --env POSTGRES_USER=working_pr \
    --env POSTGRES_PASSWORD=working-pr-local-only \
    --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,nodev,size=256m,uid=70,gid=70,mode=0700 \
    --tmpfs /var/run/postgresql:rw,noexec,nosuid,nodev,size=16m,uid=70,gid=70,mode=0775 \
    "$POSTGRES_IMAGE"
)"
REDIS_ID="$(
  /usr/bin/docker run --detach --pull never --platform linux/amd64 \
    --label "codecrow.working-pr.run=$RUN_ID" \
    --cap-drop ALL --security-opt no-new-privileges --read-only \
    --publish 127.0.0.1::6379 \
    --user redis:redis \
    --tmpfs /data:rw,noexec,nosuid,nodev,size=64m \
    "$REDIS_IMAGE" redis-server --save '' --appendonly no
)"
if [[ ! "$POSTGRES_ID" =~ ^[0-9a-f]{64}$ || ! "$REDIS_ID" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ERROR: Docker did not return full container IDs" >&2
  exit 70
fi

POSTGRES_READY_STREAK=0
for _ in $(seq 1 120); do
  if /usr/bin/docker exec "$POSTGRES_ID" \
    pg_isready -U working_pr -d working_pr >/dev/null 2>&1; then
    POSTGRES_READY_STREAK=$((POSTGRES_READY_STREAK + 1))
    [[ "$POSTGRES_READY_STREAK" -lt 3 ]] || break
  else
    POSTGRES_READY_STREAK=0
  fi
  sleep 0.25
done
if [[ "$POSTGRES_READY_STREAK" -lt 3 ]]; then
  echo "ERROR: Postgres did not become ready" >&2
  exit 70
fi

REDIS_READY=0
for _ in $(seq 1 120); do
  if [[ "$(/usr/bin/docker exec "$REDIS_ID" redis-cli ping 2>/dev/null || true)" == PONG ]]; then
    REDIS_READY=1
    break
  fi
  sleep 0.25
done
if [[ "$REDIS_READY" -ne 1 ]]; then
  echo "ERROR: Redis did not become ready" >&2
  exit 70
fi

POSTGRES_PUBLISHED="$(/usr/bin/docker port "$POSTGRES_ID" 5432/tcp)"
REDIS_PUBLISHED="$(/usr/bin/docker port "$REDIS_ID" 6379/tcp)"
if [[ ! "$POSTGRES_PUBLISHED" =~ ^127\.0\.0\.1:([0-9]+)$ ]]; then
  echo "ERROR: Postgres was not published on one loopback port" >&2
  exit 70
fi
POSTGRES_PORT="${BASH_REMATCH[1]}"
if [[ ! "$REDIS_PUBLISHED" =~ ^127\.0\.0\.1:([0-9]+)$ ]]; then
  echo "ERROR: Redis was not published on one loopback port" >&2
  exit 70
fi
REDIS_PORT="${BASH_REMATCH[1]}"

export VS01_POSTGRES_HOST=127.0.0.1
export VS01_POSTGRES_PORT="$POSTGRES_PORT"
export VS01_POSTGRES_DATABASE=working_pr
export VS01_POSTGRES_USER=working_pr
export VS01_POSTGRES_PASSWORD=working-pr-local-only
export VS01_REDIS_HOST=127.0.0.1
export VS01_REDIS_PORT="$REDIS_PORT"
export VS01_REDIS_DB=1
export REDIS_URL="redis://127.0.0.1:${REDIS_PORT}/1"
export VS01_WORKER_JOB_TIMEOUT_SECONDS="$WORKER_JOB_TIMEOUT_SECONDS"
export VS01_WORKER_LEDGER_PATH="$WORKER_LEDGER"
export VS01_WORKER_LOG_LEVEL=INFO
export CODECROW_REVIEW_DELIVERY_INITIAL_DELAY_MS=3600000

printf 'postgres=127.0.0.1:%s/working_pr\nredis=127.0.0.1:%s/1\n' \
  "$POSTGRES_PORT" "$REDIS_PORT" >"$ARTIFACT_ROOT/endpoints.txt"

(
  cd "$REPOSITORY_ROOT"
  exec "$PYTHON_BIN" "$WORKER"
) >"$WORKER_STDOUT" 2>"$WORKER_STDERR" &
WORKER_PID=$!

WORKER_READY=0
for _ in $(seq 1 120); do
  if grep -Fq '"event": "working_pr_worker_ready"' "$WORKER_STDOUT" 2>/dev/null; then
    WORKER_READY=1
    break
  fi
  if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    break
  fi
  sleep 0.25
done
if [[ "$WORKER_READY" -ne 1 ]]; then
  echo "ERROR: Python worker did not become ready" >&2
  tail -n 60 "$WORKER_STDERR" >&2 || true
  exit 70
fi

(
  cd "$REPOSITORY_ROOT"
  exec /usr/bin/mvn --offline --no-transfer-progress \
    -f java-ecosystem/pom.xml \
    -pl services/pipeline-agent -am \
    -Dtest="$JAVA_TEST_SELECTOR" \
    -Dsurefire.failIfNoSpecifiedTests=false \
    test
) >"$JAVA_LOG" 2>&1 &
JAVA_PID=$!

JAVA_STATUS=0
if wait "$JAVA_PID"; then
  JAVA_STATUS=0
else
  JAVA_STATUS=$?
fi
JAVA_PID=""
if [[ "$JAVA_STATUS" -ne 0 ]]; then
  echo "ERROR: working-PR Java test failed (exit $JAVA_STATUS)" >&2
  tail -n 100 "$JAVA_LOG" >&2 || true
  exit "$JAVA_STATUS"
fi

for _ in $(seq 1 300); do
  kill -0 "$WORKER_PID" 2>/dev/null || break
  sleep 0.1
done
if kill -0 "$WORKER_PID" 2>/dev/null; then
  echo "ERROR: Python worker did not finish its single job" >&2
  exit 70
fi
WORKER_STATUS=0
if wait "$WORKER_PID"; then
  WORKER_STATUS=0
else
  WORKER_STATUS=$?
fi
WORKER_PID=""
if [[ "$WORKER_STATUS" -ne 0 \
  || ! -s "$WORKER_LEDGER" \
  || "$(grep -Fc '"event": "working_pr_worker_complete"' "$WORKER_STDOUT" || true)" -ne 1 \
  || "$(grep -Fc '"live_external_calls": 0' "$WORKER_STDOUT" || true)" -ne 1 ]]; then
  echo "ERROR: Python worker did not publish one clean offline completion" >&2
  tail -n 80 "$WORKER_STDERR" >&2 || true
  exit 70
fi
if [[ "$(/usr/bin/docker exec "$REDIS_ID" redis-cli -n 1 LLEN codecrow:analysis:jobs)" != "0" ]]; then
  echo "ERROR: analysis queue is not empty after the completed flow" >&2
  exit 70
fi

printf 'status=PASS\nrun_id=%s\njava_selector=%s\nworker_exit=%s\n' \
  "$RUN_ID" "$JAVA_TEST_SELECTOR" "$WORKER_STATUS" >"$SUMMARY"
RUN_COMPLETE=1
echo "working PR analysis PASS"
