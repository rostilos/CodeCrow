#!/bin/sh
set -eu

for name in rag-pipeline.env rag-pipeline-newrelic.ini; do
    source="/run/codecrow-config/$name"
    if [ ! -f "$source" ]; then
        source="/app/codecrow-config/$name"
    fi
    case "$name" in
        rag-pipeline.env) target="/app/.env" ;;
        rag-pipeline-newrelic.ini) target="/app/newrelic.ini" ;;
    esac

    if [ -f "$source" ]; then
        cp "$source" "$target"
        chown appuser:appgroup "$target"
        chmod 600 "$target"
    fi
done

if [ -f /app/newrelic.ini ]; then
    export NEW_RELIC_CONFIG_FILE=/app/newrelic.ini
    exec gosu appuser newrelic-admin run-program "$@"
fi

unset NEW_RELIC_CONFIG_FILE
exec gosu appuser "$@"
