#!/usr/bin/env bash
# Small, dependency-free audit for botai's five harness subsystems.
set -u

ROOT="${1:-.}"
failures=0
pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1"; failures=$((failures + 1)); }

[ -f "$ROOT/AGENTS.md" ] && pass 'instructions: AGENTS.md exists' || fail 'instructions: AGENTS.md missing'
[ -f "$ROOT/PROGRESS.md" ] && pass 'state: PROGRESS.md exists' || fail 'state: PROGRESS.md missing'
[ -f "$ROOT/features/feature_list.json" ] && pass 'scope: feature registry exists' || fail 'scope: feature registry missing'
[ -f "$ROOT/init.sh" ] && pass 'lifecycle: init.sh exists' || fail 'lifecycle: init.sh missing'

if [ -f "$ROOT/Makefile" ] && grep -q '^check:' "$ROOT/Makefile"; then
  pass 'verification: make check exists'
else
  fail 'verification: make check missing'
fi

if [ -f "$ROOT/Makefile" ] && grep -q '^test:' "$ROOT/Makefile"; then
  pass 'verification: make test exists'
else
  fail 'verification: make test missing'
fi

if [ -f "$ROOT/AGENTS.md" ] && grep -Eiq 'verification|провер' "$ROOT/AGENTS.md"; then
  pass 'instructions: verification guidance is documented'
else
  fail 'instructions: verification guidance is missing'
fi

if [ -f "$ROOT/AGENTS.md" ] && grep -Eiq 'one feature|exactly one unfinished|одн.*функц|одн.*задач' "$ROOT/AGENTS.md"; then
  pass 'scope: one-feature boundary is documented'
else
  fail 'scope: one-feature boundary is missing'
fi

if [ -f "$ROOT/features/feature_list.json" ] && python3 - "$ROOT/features/feature_list.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as f:
    data = json.load(f)
assert isinstance(data, dict) and isinstance(data.get('features'), list)
for feature in data['features']:
    assert all(key in feature for key in ('id', 'name', 'status', 'verification'))
PY
then
  pass 'state: feature registry is valid JSON with required fields'
else
  fail 'state: feature registry is invalid'
fi

printf 'summary: %d failure(s)\n' "$failures"
[ "$failures" -eq 0 ]
