#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
BASE_COMPOSE_FILE="$REPO_ROOT/e2e_test/docker-compose.base.yml"
SOURCE_VAULT_DIR="$REPO_ROOT/e2e_test/test_vault"
SCENARIOS=(scenario1 scenario2)

source_vault_sha256() {
  python3 - "$SOURCE_VAULT_DIR" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
hashes = {}
for path in sorted(source.rglob("*")):
    if path.is_file():
        hashes[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps(hashes, sort_keys=True))
PY
}

cleanup_project() {
  local project_name="$1"
  local scenario_compose_file="$2"
  local cleanup_status=0

  set +e
  docker compose -p "$project_name" -f "$BASE_COMPOSE_FILE" -f "$scenario_compose_file" down -v --remove-orphans
  cleanup_status=$?

  if [[ "$cleanup_status" -ne 0 ]]; then
    printf 'WARNING: docker compose cleanup failed for %s with status %s\n' "$project_name" "$cleanup_status" >&2
  fi

  return "$cleanup_status"
}

run_scenario() {
  local scenario="$1"
  local PROJECT_NAME="e2e-test-${scenario}"
  local scenario_compose_file="$REPO_ROOT/e2e_test/docker-compose.${scenario}.yml"
  local pre_cleanup_status=0
  local seed_status=0
  local test_status=0
  local cleanup_status=0

  printf '\n==> Running %s\n' "$scenario"

  set +e
  cleanup_project "$PROJECT_NAME" "$scenario_compose_file"
  pre_cleanup_status=$?
  set -e

  set +e
  docker compose -p "$PROJECT_NAME" -f "$BASE_COMPOSE_FILE" -f "$scenario_compose_file" run --rm vault-seeder
  seed_status=$?
  set -e

  if [[ "$seed_status" -eq 0 ]]; then
    set +e
    docker compose -p "$PROJECT_NAME" -f "$BASE_COMPOSE_FILE" -f "$scenario_compose_file" up --build --abort-on-container-exit --exit-code-from test-env test-memory-server test-env
    test_status=$?
    set -e
  fi

  set +e
  cleanup_project "$PROJECT_NAME" "$scenario_compose_file"
  cleanup_status=$?
  set -e

  if [[ "$seed_status" -ne 0 ]]; then
    return "$seed_status"
  fi

  if [[ "$test_status" -ne 0 ]]; then
    return "$test_status"
  fi

  if [[ "$pre_cleanup_status" -ne 0 ]]; then
    return "$pre_cleanup_status"
  fi

  return "$cleanup_status"
}

cd "$REPO_ROOT"
initial_source_vault_sha256="$(source_vault_sha256)"
overall_status=0

for scenario in "${SCENARIOS[@]}"; do
  set +e
  run_scenario "$scenario"
  scenario_status=$?
  set -e

  if [[ "$scenario_status" -ne 0 ]]; then
    overall_status="$scenario_status"
    break
  fi
done

final_source_vault_sha256="$(source_vault_sha256)"
if [[ "$initial_source_vault_sha256" != "$final_source_vault_sha256" ]]; then
  printf 'WARNING: e2e_test/test_vault fixture changed during E2E run\n' >&2
  if [[ "$overall_status" -eq 0 ]]; then
    overall_status=9
  fi
fi

exit "$overall_status"
