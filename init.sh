#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

printf '%s\n' '=== botai harness initialization ==='
printf 'root: %s\n' "$ROOT"

command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'python3 is required' >&2; exit 2; }
command -v git >/dev/null 2>&1 || { printf '%s\n' 'git is required' >&2; exit 2; }

python3 scripts/cli.py doctor
python3 -m compileall -q scripts

if [ -d tests ]; then
  python3 tests/test_paths.py
  python3 tests/test_schemas.py
else
  printf '%s\n' 'warning: tests/ is absent; run make check after restoring the test suite' >&2
fi

printf '%s\n' '=== initialization complete ==='
printf '%s\n' 'Read features/feature_list.json and select exactly one unfinished feature.'
