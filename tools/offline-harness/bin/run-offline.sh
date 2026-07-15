#!/bin/bash -p
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "usage: run-offline.sh <application-test-command> [args...]" >&2
  exit 64
fi

if [[ -n "${CODECROW_BWRAP_BIN:-}" ]]; then
  echo "ERROR: refusing an override for the trusted Bubblewrap executable" >&2
  exit 69
fi
BWRAP=/usr/bin/bwrap
if [[ ! -x "$BWRAP" || "$(realpath -e "$BWRAP" 2>/dev/null || true)" != /usr/bin/bwrap ]]; then
  echo "ERROR: bubblewrap is required; refusing to run application tests without network isolation" >&2
  exit 69
fi
if ! "$BWRAP" \
  --unshare-all \
  --die-with-parent \
  --new-session \
  --ro-bind / / \
  --proc /proc \
  --dev /dev \
  -- /usr/bin/true; then
  echo "ERROR: Bubblewrap cannot create the required isolated namespaces" >&2
  exit 69
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
WORKING_DIRECTORY="$(realpath -e "$PWD")"
case "$WORKING_DIRECTORY/" in
  "$REPOSITORY_ROOT/"|"$REPOSITORY_ROOT"/*/) ;;
  *)
    echo "ERROR: offline test working directory must stay inside the repository" >&2
    exit 65
    ;;
esac

ARTIFACT_PARENT="$REPOSITORY_ROOT/.llm-handoff-artifacts"
ARTIFACT_ROOT="$ARTIFACT_PARENT/p0-03"
for artifact_directory in "$ARTIFACT_PARENT" "$ARTIFACT_ROOT"; do
  if [[ -L "$artifact_directory" \
    || ( -e "$artifact_directory" && ! -d "$artifact_directory" ) ]]; then
    echo "ERROR: offline artifact directories must be real directories, not links" >&2
    exit 65
  fi
  mkdir -p "$artifact_directory"
  RESOLVED_ARTIFACT_DIRECTORY="$(realpath -e "$artifact_directory")"
  case "$RESOLVED_ARTIFACT_DIRECTORY" in
    "$REPOSITORY_ROOT"/*) ;;
    *)
      echo "ERROR: offline artifact directory escaped the repository" >&2
      exit 65
      ;;
  esac
done
ARTIFACT_ROOT="$(realpath -e "$ARTIFACT_ROOT")"
mkdir -p "$ARTIFACT_ROOT/test-ledgers"
LEDGER_PATH="${CODECROW_EXTERNAL_CALL_LEDGER:-$REPOSITORY_ROOT/.llm-handoff-artifacts/p0-03/test-ledgers/offline-command.json}"
LEDGER_PATH="$(realpath -m "$LEDGER_PATH")"
case "$LEDGER_PATH" in
  "$ARTIFACT_ROOT"/*) ;;
  *)
    echo "ERROR: external-call ledger path must stay inside $ARTIFACT_ROOT" >&2
    exit 65
    ;;
esac
mkdir -p "$(dirname "$LEDGER_PATH")"
LEDGER_DIRECTORY="${CODECROW_EXTERNAL_CALL_LEDGER_DIR:-$ARTIFACT_ROOT/test-ledgers/java-offline-command}"
LEDGER_DIRECTORY="$(realpath -m "$LEDGER_DIRECTORY")"
case "$LEDGER_DIRECTORY" in
  "$ARTIFACT_ROOT"/*) ;;
  *)
    echo "ERROR: external-call ledger directory must stay inside $ARTIFACT_ROOT" >&2
    exit 65
    ;;
esac
mkdir -p "$LEDGER_DIRECTORY"
LEDGER_DIRECTORY="$(realpath -e "$LEDGER_DIRECTORY")"

HOST_USER_HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"
if [[ -z "$HOST_USER_HOME" || ! -d "$HOST_USER_HOME" ]]; then
  echo "ERROR: cannot determine the invoking user's trusted home directory" >&2
  exit 65
fi
HOST_USER_HOME="$(realpath -e "$HOST_USER_HOME")"
DEFAULT_MAVEN_REPOSITORY="$(realpath -m "$HOST_USER_HOME/.m2/repository")"
WORKSPACE_MAVEN_REPOSITORY="$ARTIFACT_ROOT/dependency-cache/maven"
HOST_MAVEN_REPOSITORY="$(realpath -m "${CODECROW_MAVEN_REPOSITORY:-$DEFAULT_MAVEN_REPOSITORY}")"
case "$HOST_MAVEN_REPOSITORY" in
  "$DEFAULT_MAVEN_REPOSITORY"|"$WORKSPACE_MAVEN_REPOSITORY") ;;
  *)
    echo "ERROR: Maven repository must be the user cache or P0-03 workspace cache" >&2
    exit 65
    ;;
esac
MAVEN_CACHE_ARGS=(--dir /tmp/codecrow-maven-repository)
if [[ -d "$HOST_MAVEN_REPOSITORY" ]]; then
  HOST_MAVEN_REPOSITORY="$(realpath -e "$HOST_MAVEN_REPOSITORY")"
  MAVEN_CACHE_ARGS+=(--ro-bind "$HOST_MAVEN_REPOSITORY" /tmp/codecrow-maven-repository)
fi

if [[ -n "${JAVA_HOME:-}" ]]; then
  HOST_JAVA_HOME="$(realpath -e "$JAVA_HOME")"
else
  HOST_JAVA_HOME="$(dirname "$(dirname "$(realpath -e /usr/bin/java)")")"
fi
case "$HOST_JAVA_HOME" in
  /usr/lib/jvm/*|/opt/hostedtoolcache/Java_Temurin-Hotspot_jdk/17*/*) ;;
  *)
    echo "ERROR: Java runtime is outside the approved system/setup-java roots" >&2
    exit 65
    ;;
esac
JAVA_MAJOR="$({ /usr/bin/env -i \
  PATH="$PATH" HOME=/tmp LANG=C LC_ALL=C TZ=UTC \
  "$HOST_JAVA_HOME/bin/java" -XshowSettings:properties -version; } 2>&1 \
  | sed -n 's/^[[:space:]]*java.version = \([0-9][0-9]*\).*/\1/p' \
  | head -n 1)"
if [[ "$JAVA_MAJOR" != 17 ]]; then
  echo "ERROR: offline tests require the selected Java 17 runtime" >&2
  exit 65
fi

RUNTIME_MOUNT_ARGS=()
case "$HOST_JAVA_HOME" in
  /usr/*) ;;
  *) RUNTIME_MOUNT_ARGS+=(--ro-bind "$HOST_JAVA_HOME" "$HOST_JAVA_HOME") ;;
esac

COMMAND_PATH="$1"
if [[ "$COMMAND_PATH" != */* ]]; then
  COMMAND_PATH="$(command -v -- "$COMMAND_PATH" || true)"
elif [[ "$COMMAND_PATH" != /* ]]; then
  COMMAND_PATH="$WORKING_DIRECTORY/$COMMAND_PATH"
fi
if [[ -z "$COMMAND_PATH" || ! -e "$COMMAND_PATH" ]]; then
  echo "ERROR: application-test command does not exist" >&2
  exit 66
fi
COMMAND_LEXICAL_PATH="$(realpath -ms "$COMMAND_PATH")"
COMMAND_REALPATH="$(realpath -e "$COMMAND_PATH")"
case "$COMMAND_REALPATH" in
  "$REPOSITORY_ROOT"/*|/usr/*|/bin/*|/sbin/*) ;;
  /opt/hostedtoolcache/Python/3.11.*/x64/bin/python*|\
  /opt/hostedtoolcache/Python/3.11.*/arm64/bin/python*|\
  /opt/hostedtoolcache/Python/3.11.*/bin/python*)
    PYTHON_RUNTIME_ROOT="${COMMAND_REALPATH%/bin/*}"
    PYTHON_VERSION="$(/usr/bin/env -i \
      PATH="$PATH" HOME=/tmp LANG=C LC_ALL=C TZ=UTC PYTHONNOUSERSITE=1 \
      "$COMMAND_REALPATH" -I -S -c \
      'import sys; print(".".join(map(str, sys.version_info[:2])))')"
    if [[ "$PYTHON_VERSION" != 3.11 ]]; then
      echo "ERROR: setup-python runtime must be Python 3.11" >&2
      exit 65
    fi
    RUNTIME_MOUNT_ARGS+=(--ro-bind "$PYTHON_RUNTIME_ROOT" "$PYTHON_RUNTIME_ROOT")
    ;;
  "$HOST_USER_HOME"/.pyenv/versions/3.11.*/bin/python*)
    PYTHON_RUNTIME_ROOT="${COMMAND_REALPATH%/bin/*}"
    PYTHON_VERSION="$(/usr/bin/env -i \
      PATH="$PATH" HOME=/tmp LANG=C LC_ALL=C TZ=UTC PYTHONNOUSERSITE=1 \
      "$COMMAND_REALPATH" -I -S -c \
      'import sys; print(".".join(map(str, sys.version_info[:2])))')"
    if [[ "$PYTHON_VERSION" != 3.11 ]]; then
      echo "ERROR: local locked runtime must be Python 3.11" >&2
      exit 65
    fi
    RUNTIME_MOUNT_ARGS+=(--ro-bind "$PYTHON_RUNTIME_ROOT" "$PYTHON_RUNTIME_ROOT")
    ;;
  *)
    echo "ERROR: application-test runtime is outside approved roots" >&2
    exit 65
    ;;
esac

APPROVED_CERTIFI_CA=""
case "$COMMAND_LEXICAL_PATH" in
  "$ARTIFACT_ROOT"/locked-python311/bin/python*)
    APPROVED_CERTIFI_CA="$ARTIFACT_ROOT/locked-python311/lib/python3.11/site-packages/certifi/cacert.pem"
    CERTIFI_PROVENANCE="$REPOSITORY_ROOT/tools/offline-harness/requirements/certifi-cacert.sha256"
    LOCK_FILE="$REPOSITORY_ROOT/tools/offline-harness/requirements/ci-test.lock"
    LOCK_PROVENANCE="$REPOSITORY_ROOT/tools/offline-harness/requirements/ci-test.lock.sha256"
    if [[ ! -f "$APPROVED_CERTIFI_CA" || -L "$APPROVED_CERTIFI_CA" \
      || ! -f "$CERTIFI_PROVENANCE" || ! -f "$LOCK_PROVENANCE" ]]; then
      echo "ERROR: locked Python CA-bundle provenance is incomplete" >&2
      exit 65
    fi
    EXPECTED_LOCK_SHA="$(cut -d' ' -f1 "$LOCK_PROVENANCE")"
    ACTUAL_LOCK_SHA="$(sha256sum "$LOCK_FILE" | cut -d' ' -f1)"
    EXPECTED_CERTIFI_SHA="$(cut -d' ' -f1 "$CERTIFI_PROVENANCE")"
    ACTUAL_CERTIFI_SHA="$(sha256sum "$APPROVED_CERTIFI_CA" | cut -d' ' -f1)"
    if [[ ! "$EXPECTED_LOCK_SHA" =~ ^[0-9a-f]{64}$ \
      || "$ACTUAL_LOCK_SHA" != "$EXPECTED_LOCK_SHA" \
      || ! "$EXPECTED_CERTIFI_SHA" =~ ^[0-9a-f]{64}$ \
      || "$ACTUAL_CERTIFI_SHA" != "$EXPECTED_CERTIFI_SHA" ]]; then
      echo "ERROR: locked Python CA bundle or dependency lock failed integrity verification" >&2
      exit 65
    fi
    ;;
esac

SYSTEM_MOUNT_ARGS=(--ro-bind /usr /usr)
for system_path in /bin /sbin /lib /lib64; do
  if [[ -e "$system_path" ]]; then
    SYSTEM_MOUNT_ARGS+=(--ro-bind "$system_path" "$system_path")
  fi
done
SYSTEM_MOUNT_ARGS+=(--dir /etc)
for system_path in \
  /etc/alternatives \
  /etc/java-17-openjdk \
  /etc/ld.so.cache \
  /etc/ld.so.conf \
  /etc/ld.so.conf.d \
  /etc/ssl/certs; do
  if [[ -e "$system_path" ]]; then
    SYSTEM_MOUNT_ARGS+=(--ro-bind "$system_path" "$system_path")
  fi
done
if [[ -f /etc/maven/m2.conf && -d /etc/maven/logging ]]; then
  SYSTEM_MOUNT_ARGS+=(
    --dir /etc/maven
    --ro-bind /etc/maven/m2.conf /etc/maven/m2.conf
    --ro-bind /etc/maven/logging /etc/maven/logging
  )
fi

MASKED_FILE_ARGS=()
while IFS= read -r -d '' sensitive_link; do
  echo "ERROR: refusing named credential symlink inside repository: $sensitive_link" >&2
  exit 65
done < <(
  find "$REPOSITORY_ROOT" -type l \
    \( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' \) \
    -not -path '*/.git/*' \
    -not -path '*/node_modules/*' \
    -not -path '*/target/*' \
    -print0
)
while IFS= read -r -d '' sensitive_file; do
  if [[ -n "$APPROVED_CERTIFI_CA" && "$sensitive_file" == "$APPROVED_CERTIFI_CA" ]]; then
    continue
  fi
  MASKED_FILE_ARGS+=(--ro-bind /dev/null "$sensitive_file")
done < <(
  find "$REPOSITORY_ROOT" -type f \
    \( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' \) \
    -not -path '*/.git/*' \
    -not -path '*/node_modules/*' \
    -not -path '*/target/*' \
    -print0
)

exec "$BWRAP" \
  --unshare-all \
  --die-with-parent \
  --new-session \
  --tmpfs / \
  "${SYSTEM_MOUNT_ARGS[@]}" \
  --tmpfs /run \
  --tmpfs /home \
  --tmpfs /root \
  --tmpfs /tmp \
  "${RUNTIME_MOUNT_ARGS[@]}" \
  --bind "$REPOSITORY_ROOT" "$REPOSITORY_ROOT" \
  --dir /tmp/codecrow-home \
  "${MAVEN_CACHE_ARGS[@]}" \
  --proc /proc \
  --dev /dev \
  --chdir "$WORKING_DIRECTORY" \
  --clearenv \
  --setenv PATH "$HOST_JAVA_HOME/bin:$PATH" \
  --setenv JAVA_HOME "$HOST_JAVA_HOME" \
  --setenv HOME /tmp/codecrow-home \
  --setenv USER codecrow-test \
  --setenv LOGNAME codecrow-test \
  --setenv LANG C.UTF-8 \
  --setenv LC_ALL C.UTF-8 \
  --setenv TZ UTC \
  --setenv TMPDIR /tmp \
  --setenv PYTHONDONTWRITEBYTECODE 1 \
  --setenv PYTHONHASHSEED 0 \
  --setenv MAVEN_OPTS "-Dmaven.repo.local=/tmp/codecrow-maven-repository -Duser.home=/tmp/codecrow-home" \
  --setenv CODECROW_EXTERNAL_CALL_LEDGER "$LEDGER_PATH" \
  --setenv CODECROW_EXTERNAL_CALL_LEDGER_DIR "$LEDGER_DIRECTORY" \
  --setenv INTERNAL_API_SECRET test-secret-token \
  --setenv CODECROW_INTERNAL_API_SECRET test-secret-token \
  --setenv CODECROW_RAG_API_SECRET test-secret-token \
  --setenv CODECROW_INTERNAL_SECRET test-secret-token \
  --setenv TESTCONTAINERS_RYUK_DISABLED true \
  --setenv TESTCONTAINERS_REUSE_ENABLE false \
  --setenv NO_PROXY "127.0.0.1,::1" \
  --setenv no_proxy "127.0.0.1,::1" \
  "${MASKED_FILE_ARGS[@]}" \
  -- "$@"
