#!/bin/sh
set -eu

chown -R appuser:appgroup /app/logs 2>/dev/null || true

for name in application.properties github-app-private-key.pem; do
    source="/run/codecrow-config/$name"
    target="/app/config/$name"
    if [ -f "$source" ]; then
        cp "$source" "$target"
        chown appuser:appgroup "$target"
        chmod 600 "$target"
    fi
done

exec su-exec appuser "$@"
