#!/bin/bash

# Shared parser for CI build and server deployment service selection.
# Empty input and "all" keep the existing full application deployment behavior.

CODECROW_DEPLOYABLE_SERVICES=(
  "web-server"
  "pipeline-agent"
  "inference-orchestrator"
  "rag-pipeline"
  "web-frontend"
)

CODECROW_RESOLVED_SERVICES=()

codecrow_service_usage() {
  echo "all, java, python, frontend, web-server, pipeline-agent, inference-orchestrator, rag-pipeline, web-frontend"
}

codecrow_add_resolved_service() {
  local service="$1"
  local existing

  for existing in "${CODECROW_RESOLVED_SERVICES[@]}"; do
    if [ "$existing" = "$service" ]; then
      return 0
    fi
  done

  CODECROW_RESOLVED_SERVICES+=("$service")
}

codecrow_resolve_services() {
  local input="${1:-all}"
  local raw normalized

  CODECROW_RESOLVED_SERVICES=()
  input="$(printf '%s' "$input" | tr ',;\n\t' '    ')"

  if [ -z "${input// /}" ]; then
    input="all"
  fi

  for raw in $input; do
    normalized="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
    case "$normalized" in
      all|'*')
        CODECROW_RESOLVED_SERVICES=("${CODECROW_DEPLOYABLE_SERVICES[@]}")
        return 0
        ;;
      java|java-services|jvm)
        codecrow_add_resolved_service "web-server"
        codecrow_add_resolved_service "pipeline-agent"
        ;;
      python|python-services|py)
        codecrow_add_resolved_service "inference-orchestrator"
        codecrow_add_resolved_service "rag-pipeline"
        ;;
      frontend|front-end|web-frontend|ui|web-ui)
        codecrow_add_resolved_service "web-frontend"
        ;;
      web-server)
        codecrow_add_resolved_service "web-server"
        ;;
      pipeline-agent|pipeline|agent)
        codecrow_add_resolved_service "pipeline-agent"
        ;;
      inference-orchestrator|inference|orchestrator)
        codecrow_add_resolved_service "inference-orchestrator"
        ;;
      rag-pipeline|rag)
        codecrow_add_resolved_service "rag-pipeline"
        ;;
      *)
        echo "ERROR: Unknown service selection '$raw'." >&2
        echo "Valid values: $(codecrow_service_usage)" >&2
        return 1
        ;;
    esac
  done

  if [ "${#CODECROW_RESOLVED_SERVICES[@]}" -eq 0 ]; then
    CODECROW_RESOLVED_SERVICES=("${CODECROW_DEPLOYABLE_SERVICES[@]}")
  fi
}

codecrow_join_services() {
  local separator="${1:-, }"
  local joined=""
  local service
  shift || true

  for service in "$@"; do
    if [ -z "$joined" ]; then
      joined="$service"
    else
      joined="${joined}${separator}${service}"
    fi
  done

  printf '%s' "$joined"
}

codecrow_is_full_service_set() {
  local service

  if [ "$#" -ne "${#CODECROW_DEPLOYABLE_SERVICES[@]}" ]; then
    return 1
  fi

  for service in "${CODECROW_DEPLOYABLE_SERVICES[@]}"; do
    case " $* " in
      *" $service "*) ;;
      *) return 1 ;;
    esac
  done

  return 0
}

codecrow_requires_java_artifacts() {
  local service

  for service in "$@"; do
    case "$service" in
      web-server|pipeline-agent|inference-orchestrator)
        return 0
        ;;
    esac
  done

  return 1
}

codecrow_includes_service() {
  local needle="$1"
  local service
  shift || true

  for service in "$@"; do
    if [ "$service" = "$needle" ]; then
      return 0
    fi
  done

  return 1
}
