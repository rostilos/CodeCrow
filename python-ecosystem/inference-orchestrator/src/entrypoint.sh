#!/bin/sh
set -eu

cp /run/codecrow-config/inference-orchestrator.env /app/.env
chown appuser:appuser /app/.env
chmod 600 /app/.env

exec gosu appuser "$@"
