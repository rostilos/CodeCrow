#!/bin/sh
set -eu

config="/run/codecrow-config/inference-orchestrator.env"
if [ ! -f "$config" ]; then
    config="/app/codecrow-config/inference-orchestrator.env"
fi
cp "$config" /app/.env
chown appuser:appuser /app/.env
chmod 600 /app/.env

exec gosu appuser "$@"
