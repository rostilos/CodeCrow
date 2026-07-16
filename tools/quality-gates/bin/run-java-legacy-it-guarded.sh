#!/bin/bash -p
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
JAVA_ROOT="$REPOSITORY_ROOT/java-ecosystem"
POLICY_ROOT="$REPOSITORY_ROOT/tools/quality-gates/policy"
SUPERVISOR="$SCRIPT_DIR/java-legacy-it-a-supervisor.sh"
CACHE_VALIDATOR="$SCRIPT_DIR/validate-p007-maven-cache.sh"
MAVEN_REPOSITORY="$REPOSITORY_ROOT/.llm-handoff-artifacts/p0-07/dependency-cache/maven"
IMAGE_MANIFEST="$REPOSITORY_ROOT/tools/offline-harness/requirements/persistence-images-v1.json"
LANE_POLICY="$POLICY_ROOT/java-legacy-it-container-quarantine-v1.json"

if [[ $# -ne 1 ]]; then
  echo "usage: run-java-legacy-it-guarded.sh <queue|pipeline|web>" >&2
  exit 64
fi
LANE="$1"

POSTGRES_IMAGE="postgres@sha256:e013e867e712fec275706a6c51c966f0bb0c93cfa8f51000f85a15f9865a28cb"
REDIS_IMAGE="redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99"
IMAGE_MANIFEST_SHA256="a0c1f1063fadb33cc486760abeeb0edd2a1889c790ac69e9a1a12529cf3ae71c"

case "$LANE" in
  queue)
    MODULE="libs/queue"
    ARTIFACT="codecrow-queue"
    SERVICE_PORT=16379
    CONTAINER_PORT=6379
    IMAGE="$REDIS_IMAGE"
    EXPECTED_CLASSES=3
    EXPECTED_TESTS=11
    ;;
  pipeline)
    MODULE="services/pipeline-agent"
    ARTIFACT="codecrow-pipeline-agent"
    SERVICE_PORT=15432
    CONTAINER_PORT=5432
    IMAGE="$POSTGRES_IMAGE"
    EXPECTED_CLASSES=8
    EXPECTED_TESTS=47
    ;;
  web)
    MODULE="services/web-server"
    ARTIFACT="codecrow-web-server"
    SERVICE_PORT=15432
    CONTAINER_PORT=5432
    IMAGE="$POSTGRES_IMAGE"
    EXPECTED_CLASSES=11
    EXPECTED_TESTS=113
    ;;
  *)
    echo "ERROR: unsupported guarded legacy IT lane" >&2
    exit 64
    ;;
esac

TRUSTED_TOOLS=(
  /usr/bin/bwrap
  /usr/bin/rootlesskit
  /usr/bin/socat
  /usr/bin/newuidmap
  /usr/bin/newgidmap
  /usr/sbin/ip
  /usr/bin/docker
  /usr/bin/python3
)
for tool in "${TRUSTED_TOOLS[@]}"; do
  resolved="$(realpath -e "$tool" 2>/dev/null || true)"
  if [[ -z "$resolved" || ! -f "$resolved" || ! -x "$resolved" ]]; then
    echo "ERROR: trusted guarded-lane tool is missing or redirected: $tool" >&2
    exit 69
  fi
  case "$tool:$resolved" in
    /usr/bin/socat:/usr/bin/socat|/usr/bin/socat:/usr/bin/socat1) ;;
    /usr/sbin/ip:/usr/sbin/ip|/usr/sbin/ip:/usr/bin/ip) ;;
    /usr/bin/python3:/usr/bin/python3|/usr/bin/python3:/usr/bin/python3.*) ;;
    "$tool:$tool") ;;
    *)
      echo "ERROR: trusted guarded-lane tool escaped its canonical system path: $tool" >&2
      exit 69
      ;;
  esac
  if [[ "$(stat -Lc '%u' "$tool")" != 0 \
    || -n "$(find "$resolved" -maxdepth 0 -perm /022 -print -quit)" ]]; then
    echo "ERROR: trusted guarded-lane tool ownership/mode is unsafe: $tool" >&2
    exit 69
  fi
done
if [[ ! -x "$SUPERVISOR" || -L "$SUPERVISOR" \
  || ! -x "$CACHE_VALIDATOR" || -L "$CACHE_VALIDATOR" ]]; then
  echo "ERROR: guarded A-boundary supervisor is missing or symlinked" >&2
  exit 69
fi
for required in "$MAVEN_REPOSITORY" "$IMAGE_MANIFEST" "$LANE_POLICY"; do
  if [[ -L "$required" || ! -e "$required" ]]; then
    echo "ERROR: guarded-lane prerequisite is missing or symlinked: $required" >&2
    exit 65
  fi
done
if [[ "$(sha256sum "$IMAGE_MANIFEST" | cut -d' ' -f1)" != "$IMAGE_MANIFEST_SHA256" ]]; then
  echo "ERROR: persistence image manifest identity mismatch" >&2
  exit 65
fi
CODECROW_MAVEN_REPOSITORY="$MAVEN_REPOSITORY" "$CACHE_VALIDATOR" >/dev/null
if [[ ! -f /etc/subuid || ! -f /etc/subgid ]] \
  || ! grep -Eq "^$(id -un):[0-9]+:[1-9][0-9]*$" /etc/subuid \
  || ! grep -Eq "^$(id -un):[0-9]+:[1-9][0-9]*$" /etc/subgid; then
  echo "ERROR: rootlesskit requires reviewed subordinate UID/GID ranges" >&2
  exit 69
fi

if [[ -n "${JAVA_HOME:-}" ]]; then
  HOST_JAVA_HOME="$(realpath -e "$JAVA_HOME")"
else
  HOST_JAVA_HOME="$(dirname "$(dirname "$(realpath -e /usr/bin/java)")")"
fi
case "$HOST_JAVA_HOME" in
  /usr/lib/jvm/*|/opt/hostedtoolcache/Java_Temurin-Hotspot_jdk/17*/*) ;;
  *)
    echo "ERROR: guarded legacy IT Java runtime is outside approved roots" >&2
    exit 65
    ;;
esac
JAVA_MAJOR="$({ /usr/bin/env -i \
  PATH="$PATH" HOME=/tmp LANG=C LC_ALL=C TZ=UTC \
  "$HOST_JAVA_HOME/bin/java" -XshowSettings:properties -version; } 2>&1 \
  | sed -n 's/^[[:space:]]*java.version = \([0-9][0-9]*\).*/\1/p' \
  | head -n 1)"
if [[ "$JAVA_MAJOR" != 17 ]]; then
  echo "ERROR: guarded legacy IT requires Java 17" >&2
  exit 65
fi

MODULE_TARGET="$JAVA_ROOT/$MODULE/target"
if [[ -L "$MODULE_TARGET" || ! -d "$MODULE_TARGET" ]]; then
  echo "ERROR: guarded lane requires the completed offline prebuild target" >&2
  exit 65
fi
if [[ ! -d "$MODULE_TARGET/test-classes" || ! -d "$MODULE_TARGET/classes" ]]; then
  echo "ERROR: guarded lane prebuild is incomplete" >&2
  exit 65
fi

BASELINE_IDS="$(/usr/bin/docker container ls --all --quiet --no-trunc | LC_ALL=C sort)"
RUN_TOKEN="$(tr -d '-' </proc/sys/kernel/random/uuid | cut -c1-24)"
RUN_ID="p007_${RUN_TOKEN}"
NAMESPACE="codecrow-p007-${RUN_TOKEN}-${LANE}"
TASK_PARENT="$REPOSITORY_ROOT/.llm-handoff-artifacts/p0-07/java-legacy-it"
TASK_ROOT="$TASK_PARENT/$RUN_ID/$LANE"
if [[ -e "$TASK_ROOT" || -L "$TASK_ROOT" ]]; then
  echo "ERROR: guarded lane task namespace already exists" >&2
  exit 65
fi
mkdir -p "$TASK_ROOT/host-proxy" "$TASK_ROOT/evidence"
chmod 0700 "$TASK_PARENT" "$TASK_PARENT/$RUN_ID" "$TASK_ROOT" \
  "$TASK_ROOT/host-proxy" "$TASK_ROOT/evidence"
TASK_ROOT="$(realpath -e "$TASK_ROOT")"
HOST_PROXY_DIRECTORY="$TASK_ROOT/host-proxy"
EVIDENCE_DIRECTORY="$TASK_ROOT/evidence"
PULL_EVENTS="$EVIDENCE_DIRECTORY/pull-events.log"
CONTAINER_REPORT="$EVIDENCE_DIRECTORY/container.json"
ABSENCE_REPORT="$EVIDENCE_DIRECTORY/container-absence.txt"
RECEIPT="$EVIDENCE_DIRECTORY/provisioning.receipt"
TOOL_IDENTITIES="$EVIDENCE_DIRECTORY/tool-identities.sha256"
CONTAINER_ID=""
HOST_PROXY_PID=""
EVENT_PID=""
ROOTLESSKIT_STATE=""

cleanup_owned() {
  set +e
  if [[ -n "$HOST_PROXY_PID" ]] && kill -0 "$HOST_PROXY_PID" 2>/dev/null; then
    kill "$HOST_PROXY_PID" 2>/dev/null
    wait "$HOST_PROXY_PID" 2>/dev/null
  fi
  if [[ -n "$CONTAINER_ID" && "$CONTAINER_ID" =~ ^[0-9a-f]{64}$ ]]; then
    owned_run="$(/usr/bin/docker inspect --format '{{ index .Config.Labels "codecrow.p007.run" }}' "$CONTAINER_ID" 2>/dev/null || true)"
    if [[ "$owned_run" == "$RUN_ID" ]]; then
      /usr/bin/docker rm --force "$CONTAINER_ID" >/dev/null 2>&1 || true
    fi
  fi
  if [[ -n "$EVENT_PID" ]] && kill -0 "$EVENT_PID" 2>/dev/null; then
    kill "$EVENT_PID" 2>/dev/null
    wait "$EVENT_PID" 2>/dev/null
  fi
  if [[ -n "$ROOTLESSKIT_STATE" \
    && "$ROOTLESSKIT_STATE" == "/tmp/codecrow-p007-rk-${RUN_TOKEN}-${LANE}" ]]; then
    /usr/bin/rm -rf -- "$ROOTLESSKIT_STATE"
  fi
  set -e
}
trap cleanup_owned EXIT HUP INT TERM

for tool in $(printf '%s\n' "${TRUSTED_TOOLS[@]}" | LC_ALL=C sort); do
  printf '%s  %s\n' "$(sha256sum "$tool" | cut -d' ' -f1)" "$tool" \
    >>"$TOOL_IDENTITIES"
done
START_EPOCH="$(date +%s)"
: >"$PULL_EVENTS"
/usr/bin/docker events \
  --since "$START_EPOCH" \
  --filter type=image \
  --filter event=pull \
  --format '{{json .}}' >>"$PULL_EVENTS" 2>&1 &
EVENT_PID=$!

/usr/bin/docker image inspect "$IMAGE" >/dev/null
CIDFILE="$EVIDENCE_DIRECTORY/container.cid"
COMMON_RUN_ARGS=(
  run --detach --pull never --cidfile "$CIDFILE"
  --label "codecrow.p007.run=$RUN_ID"
  --label "codecrow.p007.namespace=$NAMESPACE"
  --label "codecrow.p007.lane=$LANE"
  --cap-drop ALL --security-opt no-new-privileges --read-only
  --publish "127.0.0.1::${CONTAINER_PORT}"
)
if [[ "$LANE" == queue ]]; then
  /usr/bin/docker "${COMMON_RUN_ARGS[@]}" \
    --user redis:redis \
    --tmpfs /data:rw,noexec,nosuid,nodev,size=64m \
    "$IMAGE" redis-server --save '' --appendonly no >/dev/null
else
  /usr/bin/docker "${COMMON_RUN_ARGS[@]}" \
    --user postgres:postgres \
    --env POSTGRES_DB=p007_acceptance \
    --env POSTGRES_USER=offline_fixture \
    --env POSTGRES_PASSWORD=offline_fixture_only \
    --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,nodev,size=256m,uid=70,gid=70,mode=0700 \
    --tmpfs /var/run/postgresql:rw,noexec,nosuid,nodev,size=16m,uid=70,gid=70,mode=0775 \
    "$IMAGE" >/dev/null
fi
CONTAINER_ID="$(<"$CIDFILE")"
if [[ ! "$CONTAINER_ID" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ERROR: Docker did not return one full task-owned container id" >&2
  exit 70
fi

POSTGRES_READY_STREAK=0
for _ in $(seq 1 120); do
  if [[ "$LANE" == queue ]]; then
    health="$(/usr/bin/docker exec "$CONTAINER_ID" redis-cli ping 2>/dev/null || true)"
    [[ "$health" != PONG ]] || break
  else
    if /usr/bin/docker exec "$CONTAINER_ID" \
      pg_isready -U offline_fixture -d p007_acceptance >/dev/null 2>&1; then
      POSTGRES_READY_STREAK=$((POSTGRES_READY_STREAK + 1))
      [[ "$POSTGRES_READY_STREAK" -lt 3 ]] || break
    else
      POSTGRES_READY_STREAK=0
    fi
  fi
  sleep 0.25
done
if [[ "$LANE" == queue ]]; then
  [[ "$(/usr/bin/docker exec "$CONTAINER_ID" redis-cli ping 2>/dev/null || true)" == PONG ]] \
    || { echo "ERROR: task Redis did not become ready" >&2; exit 70; }
else
  [[ "$POSTGRES_READY_STREAK" -ge 3 ]] \
    && /usr/bin/docker exec "$CONTAINER_ID" \
    pg_isready -U offline_fixture -d p007_acceptance >/dev/null 2>&1 \
    || { echo "ERROR: task PostgreSQL did not become ready" >&2; exit 70; }
fi

PUBLISHED="$(/usr/bin/docker port "$CONTAINER_ID" "${CONTAINER_PORT}/tcp")"
if [[ ! "$PUBLISHED" =~ ^127\.0\.0\.1:([0-9]+)$ ]]; then
  echo "ERROR: task service is not published on one dynamic IPv4 loopback port" >&2
  exit 70
fi
HOST_PORT="${BASH_REMATCH[1]}"
SOCKET_PATH="$HOST_PROXY_DIRECTORY/service.sock"
(
  cd -- "$HOST_PROXY_DIRECTORY"
  exec /usr/bin/socat \
    "UNIX-LISTEN:service.sock,fork,unlink-early,mode=0600" \
    "TCP4:127.0.0.1:${HOST_PORT}"
) >"$EVIDENCE_DIRECTORY/host-proxy.log" 2>&1 &
HOST_PROXY_PID=$!
for _ in $(seq 1 50); do
  [[ ! -S "$SOCKET_PATH" ]] || break
  sleep 0.1
done
if [[ ! -S "$SOCKET_PATH" ]]; then
  echo "ERROR: host capability socket failed to start" >&2
  exit 70
fi

POLICY_SHA256="$(sha256sum "$LANE_POLICY" | cut -d' ' -f1)"
printf '%s\n' \
  'schemaVersion=1' \
  "runId=$RUN_ID" \
  "lane=$LANE" \
  "targetArtifact=$ARTIFACT" \
  "namespace=$NAMESPACE" \
  "policySha256=$POLICY_SHA256" \
  "imageManifestSha256=$IMAGE_MANIFEST_SHA256" \
  "imageReference=$IMAGE" \
  "containerId=$CONTAINER_ID" \
  'serviceHost=127.0.0.1' \
  "servicePort=$SERVICE_PORT" >"$RECEIPT"
chmod 0400 "$RECEIPT"
printf '{"schemaVersion":1,"runId":"%s","lane":"%s","namespace":"%s","containerId":"%s","imageReference":"%s"}\n' \
  "$RUN_ID" "$LANE" "$NAMESPACE" "$CONTAINER_ID" "$IMAGE" >"$CONTAINER_REPORT"
chmod 0400 "$CONTAINER_REPORT"

if [[ -d "$MODULE_TARGET/failsafe-reports" ]]; then
  find "$MODULE_TARGET/failsafe-reports" -mindepth 1 -delete
else
  mkdir -p "$MODULE_TARGET/failsafe-reports"
fi
rm -f "$MODULE_TARGET/jacoco-it.exec" "$MODULE_TARGET/jacoco.exec"

ROOTLESSKIT_STATE="/tmp/codecrow-p007-rk-${RUN_TOKEN}-${LANE}"
if [[ -e "$ROOTLESSKIT_STATE" || -L "$ROOTLESSKIT_STATE" ]]; then
  echo "ERROR: guarded rootlesskit state namespace already exists" >&2
  exit 65
fi
mkdir -p "$ROOTLESSKIT_STATE"
chmod 0700 "$ROOTLESSKIT_STATE"
/usr/bin/env -i \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  HOME="${HOME:?}" \
  JAVA_HOME="$HOST_JAVA_HOME" \
  USER="$(id -un)" \
  LOGNAME="$(id -un)" \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC \
  CODECROW_P007_RUN_ID="$RUN_ID" \
  /usr/bin/rootlesskit \
    --net=none \
    --pidns \
    --utsns \
    --ipcns \
    --reaper=true \
    --state-dir="$ROOTLESSKIT_STATE" \
    "$SUPERVISOR" \
      "$REPOSITORY_ROOT" \
      "$MODULE_TARGET" \
      "$EVIDENCE_DIRECTORY" \
      "$MAVEN_REPOSITORY" \
      "$HOST_PROXY_DIRECTORY" \
      "$SERVICE_PORT" \
      "$LANE" \
      "$HOST_JAVA_HOME"

cleanup_owned
HOST_PROXY_PID=""
EVENT_PID=""
printf 'absent %s\n' "$CONTAINER_ID" >"$ABSENCE_REPORT"
if /usr/bin/docker container inspect "$CONTAINER_ID" >/dev/null 2>&1; then
  echo "ERROR: task-owned container remains after teardown" >&2
  exit 70
fi
AFTER_IDS="$(/usr/bin/docker container ls --all --quiet --no-trunc | LC_ALL=C sort)"
if [[ "$AFTER_IDS" != "$BASELINE_IDS" ]]; then
  echo "ERROR: Docker container inventory changed outside exact task ownership" >&2
  exit 70
fi
if [[ -s "$PULL_EVENTS" ]]; then
  echo "ERROR: guarded lane observed an image pull" >&2
  exit 70
fi

/usr/bin/python3 "$SCRIPT_DIR/../quality_gates/java_legacy_it.py" \
  guarded \
  --lane "$LANE" \
  --run-id "$RUN_ID" \
  --expected-classes "$EXPECTED_CLASSES" \
  --expected-tests "$EXPECTED_TESTS" \
  --report-directory "$MODULE_TARGET/failsafe-reports" \
  --ledger "$EVIDENCE_DIRECTORY/legacy-container-it-${LANE}-${RUN_ID}.json" \
  --receipt "$RECEIPT" \
  --container-report "$CONTAINER_REPORT" \
  --absence-report "$ABSENCE_REPORT" \
  --pull-events "$PULL_EVENTS"

trap - EXIT HUP INT TERM
printf 'guarded legacy IT lane PASS: %s %s\n' "$LANE" "$TASK_ROOT"
