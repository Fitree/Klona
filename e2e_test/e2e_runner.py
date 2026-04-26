#!/usr/bin/env python3
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_VAULT_DIR = REPO_ROOT / "e2e_test" / "test_vault"
RUNTIME_VAULT_DIR = Path("/runtime-vault")
SCENARIOS = [
    REPO_ROOT / "e2e_test" / "e2e_scenario1.py",
    REPO_ROOT / "e2e_test" / "e2e_scenario2.py",
]


def phase(name):
    print(f"\n==> {name}", flush=True)


def source_vault_hashes():
    if not SOURCE_VAULT_DIR.is_dir():
        raise SystemExit(f"source vault fixture is missing: {SOURCE_VAULT_DIR}")

    hashes = {}
    for path in sorted(SOURCE_VAULT_DIR.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(SOURCE_VAULT_DIR).as_posix()
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def reset_runtime_vault():
    if not RUNTIME_VAULT_DIR.is_dir():
        raise SystemExit(f"runtime vault mount is missing: {RUNTIME_VAULT_DIR}")

    for child in RUNTIME_VAULT_DIR.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    shutil.copytree(SOURCE_VAULT_DIR, RUNTIME_VAULT_DIR, dirs_exist_ok=True)


def assert_source_vault_unchanged(expected_hashes):
    actual_hashes = source_vault_hashes()
    if actual_hashes != expected_hashes:
        raise SystemExit("source e2e_test/test_vault fixture was mutated")


def run_scenario(path):
    phase(f"Reset runtime vault for {path.name}")
    reset_runtime_vault()

    phase(f"Run {path.name}")
    subprocess.run([sys.executable, "-B", str(path)], cwd=REPO_ROOT, check=True)


def main():
    expected_source_hashes = source_vault_hashes()
    try:
        for scenario in SCENARIOS:
            run_scenario(scenario)
    finally:
        assert_source_vault_unchanged(expected_source_hashes)

    print("\nALL E2E SCENARIOS PASS", flush=True)


if __name__ == "__main__":
    main()
