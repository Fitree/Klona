#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

docker build -t klona-sandbox:dev -f "$REPO_ROOT/sandbox/Dockerfile" "$REPO_ROOT"
