#!/bin/bash -p
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
set -euo pipefail
umask 077

if [[ $# -ne 8 ]]; then
  echo "usage: java-legacy-it-a-supervisor.sh <repo> <target> <artifacts> <maven-repo> <host-proxy> <port> <lane> <java-home>" >&2
  exit 64
fi

REPOSITORY_ROOT="$1"
MODULE_TARGET="$2"
ARTIFACT_DIRECTORY="$3"
MAVEN_REPOSITORY="$4"
HOST_PROXY_DIRECTORY="$5"
SERVICE_PORT="$6"
LANE="$7"
JAVA_HOME_ROOT="$8"

for directory in \
  "$REPOSITORY_ROOT" "$MODULE_TARGET" "$ARTIFACT_DIRECTORY" \
  "$MAVEN_REPOSITORY" "$HOST_PROXY_DIRECTORY"; do
  if [[ -L "$directory" || ! -d "$directory" ]]; then
    echo "ERROR: guarded A-boundary directory is missing or symlinked: $directory" >&2
    exit 65
  fi
done
JAVA_HOME_ROOT="$(realpath -e "$JAVA_HOME_ROOT" 2>/dev/null || true)"
case "$JAVA_HOME_ROOT" in
  /usr/lib/jvm/*|/opt/hostedtoolcache/Java_Temurin-Hotspot_jdk/17*/*) ;;
  *)
    echo "ERROR: guarded A-boundary Java runtime is outside approved roots" >&2
    exit 65
    ;;
esac
if [[ ! -x "$JAVA_HOME_ROOT/bin/java" ]]; then
  echo "ERROR: guarded A-boundary Java runtime is incomplete" >&2
  exit 65
fi
if [[ ! "$SERVICE_PORT" =~ ^[0-9]+$ || "$SERVICE_PORT" -lt 1 || "$SERVICE_PORT" -gt 65535 ]]; then
  echo "ERROR: guarded service port is invalid" >&2
  exit 65
fi
case "$LANE" in
  queue)
    MODULE="libs/queue"
    PROFILE="p007-guarded-queue-it"
    SELECTORS="org.rostilos.codecrow.queue.ConnectionFactoryIT,org.rostilos.codecrow.queue.QueueIsolationIT,org.rostilos.codecrow.queue.RedisQueueIT"
    REACTOR_MODULES=(
      libs/core
      libs/queue
      libs/test-support
    )
    ;;
  pipeline)
    MODULE="services/pipeline-agent"
    PROFILE="p007-guarded-pipeline-it"
    SELECTORS="org.rostilos.codecrow.pipelineagent.BranchResolverFlowIT,org.rostilos.codecrow.pipelineagent.ExecutionManifestPersistenceIT,org.rostilos.codecrow.pipelineagent.HealthCheckControllerIT,org.rostilos.codecrow.pipelineagent.LineTrackingFlowIT,org.rostilos.codecrow.pipelineagent.PipelineActionControllerIT,org.rostilos.codecrow.pipelineagent.PipelineAgentSecurityIT,org.rostilos.codecrow.pipelineagent.ProviderWebhookControllerIT,org.rostilos.codecrow.pipelineagent.RagIndexingControllerIT"
    REACTOR_MODULES=(
      libs/analysis-api
      libs/analysis-engine
      libs/ast-parser
      libs/commit-graph
      libs/core
      libs/events
      libs/file-content
      libs/queue
      libs/rag-engine
      libs/security
      libs/task-management
      libs/test-support
      libs/vcs-client
      services/pipeline-agent
    )
    ;;
  web)
    MODULE="services/web-server"
    PROFILE="p007-guarded-web-it"
    SELECTORS="org.rostilos.codecrow.webserver.AuthControllerIT,org.rostilos.codecrow.webserver.HealthCheckControllerIT,org.rostilos.codecrow.webserver.InternalApiSecurityIT,org.rostilos.codecrow.webserver.LlmModelControllerIT,org.rostilos.codecrow.webserver.ManagedImmutableManifestFlywayIT,org.rostilos.codecrow.webserver.ProjectControllerIT,org.rostilos.codecrow.webserver.PublicSiteConfigControllerIT,org.rostilos.codecrow.webserver.QualityGateControllerIT,org.rostilos.codecrow.webserver.TaskManagementControllerIT,org.rostilos.codecrow.webserver.UserDataControllerIT,org.rostilos.codecrow.webserver.WorkspaceControllerIT"
    REACTOR_MODULES=(
      libs/analysis-api
      libs/analysis-engine
      libs/ast-parser
      libs/commit-graph
      libs/core
      libs/email
      libs/events
      libs/file-content
      libs/queue
      libs/security
      libs/task-management
      libs/test-support
      libs/vcs-client
      services/web-server
    )
    ;;
  *)
    echo "ERROR: unsupported guarded legacy IT lane" >&2
    exit 64
    ;;
esac
if [[ "$MODULE_TARGET" != "$REPOSITORY_ROOT/java-ecosystem/$MODULE/target" ]]; then
  echo "ERROR: guarded target directory does not match its lane" >&2
  exit 65
fi
REACTOR_ROOT_TARGET="$REPOSITORY_ROOT/java-ecosystem/target"
if [[ -L "$REACTOR_ROOT_TARGET" || ! -d "$REACTOR_ROOT_TARGET" \
  || "$(realpath -e "$REACTOR_ROOT_TARGET" 2>/dev/null || true)" != "$REACTOR_ROOT_TARGET" \
  || -n "$(find "$REACTOR_ROOT_TARGET" -type l -print -quit)" ]]; then
  echo "ERROR: guarded reactor requires a safe completed root target" >&2
  exit 65
fi
WRITABLE_TARGET_MOUNTS=(--bind "$REACTOR_ROOT_TARGET" "$REACTOR_ROOT_TARGET")
for reactor_module in "${REACTOR_MODULES[@]}"; do
  reactor_target="$REPOSITORY_ROOT/java-ecosystem/$reactor_module/target"
  if [[ -L "$reactor_target" || ! -d "$reactor_target" \
    || "$(realpath -e "$reactor_target" 2>/dev/null || true)" != "$reactor_target" \
    || ! -d "$reactor_target/classes" \
    || -n "$(find "$reactor_target" -type l -print -quit)" ]]; then
    echo "ERROR: guarded reactor requires a safe completed prebuild target: $reactor_module" >&2
    exit 65
  fi
  WRITABLE_TARGET_MOUNTS+=(--bind "$reactor_target" "$reactor_target")
done
RECEIPT="$ARTIFACT_DIRECTORY/provisioning.receipt"
if [[ -L "$RECEIPT" || ! -f "$RECEIPT" || "$(stat -c '%a' "$RECEIPT")" != 400 ]]; then
  echo "ERROR: guarded provisioning receipt is missing or has an unsafe mode" >&2
  exit 65
fi
RECEIPT_SHA256="$(sha256sum "$RECEIPT" | cut -d' ' -f1)"

mount --make-rprivate /
mount -t tmpfs -o mode=0755,nosuid,nodev,noexec tmpfs /run
mkdir -p /run/codecrow-host-proxy
mount --bind "$HOST_PROXY_DIRECTORY" /run/codecrow-host-proxy
mount -o remount,bind,ro /run/codecrow-host-proxy
for forbidden in \
  /run/docker.sock /var/run/docker.sock \
  /run/containerd/containerd.sock /var/run/containerd/containerd.sock \
  /run/podman/podman.sock /var/run/podman/podman.sock; do
  if [[ -e "$forbidden" || -L "$forbidden" ]]; then
    echo "ERROR: A boundary can see a forbidden container control path" >&2
    exit 69
  fi
done

SOCAT_LOG="$ARTIFACT_DIRECTORY/a-loopback-proxy.log"
/usr/bin/socat \
  "TCP4-LISTEN:${SERVICE_PORT},bind=127.0.0.1,reuseaddr,fork" \
  UNIX-CONNECT:/run/codecrow-host-proxy/service.sock \
  >"$SOCAT_LOG" 2>&1 &
A_SOCAT_PID=$!
cleanup() {
  if kill -0 "$A_SOCAT_PID" 2>/dev/null; then
    kill "$A_SOCAT_PID" 2>/dev/null || true
    wait "$A_SOCAT_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT HUP INT TERM

for _ in $(seq 1 50); do
  if /usr/bin/socat -T 1 - "TCP4:127.0.0.1:${SERVICE_PORT}" </dev/null >/dev/null 2>&1; then
    break
  fi
  /usr/bin/sleep 0.1
done
if ! kill -0 "$A_SOCAT_PID" 2>/dev/null; then
  echo "ERROR: A loopback capability proxy failed to start" >&2
  exit 69
fi

SYSTEM_MOUNTS=(--ro-bind /usr /usr)
for path in /bin /sbin /lib /lib64; do
  [[ ! -e "$path" ]] || SYSTEM_MOUNTS+=(--ro-bind "$path" "$path")
done
case "$JAVA_HOME_ROOT" in
  /usr/*) ;;
  *) SYSTEM_MOUNTS+=(--ro-bind "$JAVA_HOME_ROOT" "$JAVA_HOME_ROOT") ;;
esac
SYSTEM_MOUNTS+=(--dir /etc)
for path in \
  /etc/alternatives /etc/group /etc/java-17-openjdk /etc/ld.so.cache \
  /etc/ld.so.conf /etc/ld.so.conf.d /etc/maven /etc/nsswitch.conf \
  /etc/passwd /etc/ssl/certs; do
  [[ ! -e "$path" ]] || SYSTEM_MOUNTS+=(--ro-bind "$path" "$path")
done

CLOSE_INHERITED_FDS='for descriptor_path in /proc/$$/fd/*; do
  descriptor=${descriptor_path##*/}
  if [[ "$descriptor" =~ ^[0-9]+$ && "$descriptor" -gt 2 ]]; then
    exec {descriptor}>&-
  fi
done
exec "$@"'

if /bin/bash -p -c "$CLOSE_INHERITED_FDS" codecrow-close-inherited-fds \
  /usr/bin/bwrap \
  --unshare-all \
  --share-net \
  --unshare-user \
  --disable-userns \
  --die-with-parent \
  --new-session \
  --uid 0 \
  --gid 0 \
  --cap-drop ALL \
  --tmpfs / \
  "${SYSTEM_MOUNTS[@]}" \
  --tmpfs /run \
  --tmpfs /home \
  --tmpfs /root \
  --tmpfs /tmp \
  --dir /tmp/codecrow-home \
  --ro-bind "$REPOSITORY_ROOT" "$REPOSITORY_ROOT" \
  --tmpfs "$REPOSITORY_ROOT/.llm-handoff-artifacts" \
  "${WRITABLE_TARGET_MOUNTS[@]}" \
  --bind "$ARTIFACT_DIRECTORY" /codecrow-artifacts \
  --ro-bind "$MAVEN_REPOSITORY" /tmp/codecrow-maven-repository \
  --proc /proc \
  --dev /dev \
  --chdir "$REPOSITORY_ROOT" \
  --clearenv \
  --setenv PATH "$JAVA_HOME_ROOT/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  --setenv JAVA_HOME "$JAVA_HOME_ROOT" \
  --setenv HOME /tmp/codecrow-home \
  --setenv USER codecrow-test \
  --setenv LOGNAME codecrow-test \
  --setenv LANG C.UTF-8 \
  --setenv LC_ALL C.UTF-8 \
  --setenv TZ UTC \
  --setenv TMPDIR /tmp \
  --setenv LOGGING_FILE_NAME /codecrow-artifacts/application.log \
  --setenv LOGGING_FILE_PATTERN '/codecrow-artifacts/application-%d{yyyy-MM-dd}.log' \
  --setenv MAVEN_OPTS "-Dmaven.repo.local=/tmp/codecrow-maven-repository -Duser.home=/tmp/codecrow-home" \
  --setenv INTERNAL_API_SECRET test-secret-token \
  --setenv CODECROW_INTERNAL_API_SECRET test-secret-token \
  --setenv CODECROW_RAG_API_SECRET test-secret-token \
  --setenv CODECROW_INTERNAL_SECRET test-secret-token \
  --setenv NO_PROXY 127.0.0.1,::1 \
  --setenv no_proxy 127.0.0.1,::1 \
  -- /usr/bin/mvn \
    --offline \
    --no-transfer-progress \
    --settings "$REPOSITORY_ROOT/tools/offline-harness/maven/settings-ci.xml" \
    --file "$REPOSITORY_ROOT/java-ecosystem/pom.xml" \
    --projects "$MODULE" \
    --also-make \
    --activate-profiles "quality-coverage,p007-integration-only,${PROFILE}" \
    "-Dit.test=${SELECTORS}" \
    -Dfailsafe.failIfNoSpecifiedTests=false \
    "-Dp007.run-id=${CODECROW_P007_RUN_ID:?}" \
    -Dp007.ledger-directory=/codecrow-artifacts \
    "-Dp007.provisioning-receipt-sha256=${RECEIPT_SHA256}" \
    verify; then
  BOUNDARY_STATUS=0
else
  BOUNDARY_STATUS=$?
fi
cleanup
trap - EXIT HUP INT TERM
exit "$BOUNDARY_STATUS"
