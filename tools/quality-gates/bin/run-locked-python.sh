#!/bin/bash -p
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
LOCK="$REPOSITORY_ROOT/tools/offline-harness/requirements/ci-test.lock"
LOCK_PROVENANCE="$REPOSITORY_ROOT/tools/offline-harness/requirements/ci-test.lock.sha256"
PORTABLE_ROOT="$REPOSITORY_ROOT/.llm-handoff-artifacts/p0-07/portable-python311"

verify_lock() {
  local expected actual
  expected="$(cut -d' ' -f1 "$LOCK_PROVENANCE")"
  actual="$(sha256sum "$LOCK" | cut -d' ' -f1)"
  if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$actual" != "$expected" ]]; then
    echo "ERROR: the frozen P0-03 Python dependency lock failed verification" >&2
    exit 65
  fi
}

prepare_runtime() {
  if [[ $# -ne 1 ]]; then
    echo "usage: run-locked-python.sh --prepare <locked-python-venv>" >&2
    exit 64
  fi
  local supplied_venv source_venv source_python base_root stage version
  supplied_venv="$1"
  if [[ -L "$supplied_venv" || ! -d "$supplied_venv" ]]; then
    echo "ERROR: the supplied locked Python environment is unavailable" >&2
    exit 66
  fi
  source_venv="$(realpath -e "$supplied_venv")"
  case "$source_venv" in
    "$REPOSITORY_ROOT"/*) ;;
    *)
      echo "ERROR: the supplied locked Python environment escaped the repository" >&2
      exit 65
      ;;
  esac
  source_python="$(realpath -e "$source_venv/bin/python")"
  base_root="$(dirname "$(dirname "$source_python")")"
  case "$source_python" in
    /home/*/.pyenv/versions/3.11.*/bin/python*|\
    /opt/hostedtoolcache/Python/3.11.*/x64/bin/python*|\
    /opt/hostedtoolcache/Python/3.11.*/arm64/bin/python*|\
    /opt/hostedtoolcache/Python/3.11.*/bin/python*) ;;
    *)
      echo "ERROR: the locked Python base runtime is outside approved roots" >&2
      exit 65
      ;;
  esac
  version="$(/usr/bin/env -i PATH="$PATH" HOME=/tmp LANG=C LC_ALL=C TZ=UTC \
    "$source_python" -I -S -c \
    'import sys; print(".".join(map(str, sys.version_info[:2])))')"
  if [[ "$version" != 3.11 \
    || ! -f "$base_root/lib/libpython3.11.so.1.0" \
    || ! -d "$base_root/lib/python3.11" \
    || ! -d "$source_venv/lib/python3.11/site-packages" ]]; then
    echo "ERROR: the locked Python 3.11 runtime is incomplete" >&2
    exit 66
  fi

  verify_lock
  mkdir -p "$(dirname "$PORTABLE_ROOT")"
  stage="$(mktemp -d "$(dirname "$PORTABLE_ROOT")/.portable-python311.XXXXXXXX")"
  trap '/usr/bin/rm -rf -- "$stage"' RETURN
  mkdir -p "$stage/bin" "$stage/lib/python3.11"
  cp --reflink=auto "$source_python" "$stage/bin/python3.11"
  cp --reflink=auto "$base_root/lib/libpython3.11.so.1.0" "$stage/lib/"
  rsync -a --delete \
    --exclude=site-packages --exclude=__pycache__ --exclude='*.pyc' \
    --link-dest="$base_root/lib/python3.11" \
    "$base_root/lib/python3.11/" "$stage/lib/python3.11/"
  mkdir -p "$stage/lib/python3.11/site-packages"
  rsync -a --delete \
    --exclude=__pycache__ --exclude='*.pyc' \
    --link-dest="$source_venv/lib/python3.11/site-packages" \
    "$source_venv/lib/python3.11/site-packages/" \
    "$stage/lib/python3.11/site-packages/"
  chmod 0555 "$stage/bin/python3.11"
  /usr/bin/rm -rf -- "$PORTABLE_ROOT"
  mv "$stage" "$PORTABLE_ROOT"
  trap - RETURN
}

if [[ "${1:-}" == --prepare ]]; then
  shift
  prepare_runtime "$@"
  exit 0
fi

verify_lock
PYTHON="$PORTABLE_ROOT/bin/python3.11"
LIBPYTHON="$PORTABLE_ROOT/lib/libpython3.11.so.1.0"
SITE_PACKAGES="$PORTABLE_ROOT/lib/python3.11/site-packages"
if [[ -L "$PYTHON" || ! -x "$PYTHON" \
  || -L "$LIBPYTHON" || ! -f "$LIBPYTHON" \
  || -L "$SITE_PACKAGES" || ! -d "$SITE_PACKAGES" ]]; then
  echo "ERROR: prepare the portable frozen Python 3.11 runtime first" >&2
  exit 66
fi

export LD_LIBRARY_PATH="$PORTABLE_ROOT/lib"
export PYTHONHOME="$PORTABLE_ROOT"
export PYTHONPATH="$SITE_PACKAGES"
export PYTHONNOUSERSITE=1
exec "$PYTHON" "$@"
