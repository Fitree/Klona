#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/sandbox/docker-compose.e2e.yml"
PROJECT_NAME="sandbox"

cleanup() {
  local status=$?
  local cleanup_status=0

  set +e
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down -v --remove-orphans
  cleanup_status=$?
  set -e

  if [[ "$cleanup_status" -ne 0 ]]; then
    printf 'WARNING: docker compose cleanup failed with status %s\n' "$cleanup_status" >&2
  fi

  if [[ "$status" -ne 0 ]]; then
    exit "$status"
  fi

  exit "$cleanup_status"
}

trap cleanup EXIT

cd "$REPO_ROOT"
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up --build --abort-on-container-exit --exit-code-from test-env
