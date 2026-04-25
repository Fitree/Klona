#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

docker run --rm -it -v "$REPO_ROOT:/workspace/KLONA" -w /workspace/KLONA klona-sandbox:dev
