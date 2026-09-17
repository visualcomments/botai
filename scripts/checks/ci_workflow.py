"""Validate the CI workflow: it must parse, and every step must be shaped right.

A workflow that does not parse fails on GitHub with a message about the file
rather than the step, and it fails *after* the push. Checking locally costs a
second.

Run:
    python scripts/checks/ci_workflow.py
"""
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("pyyaml отсутствует — проверка пропущена")
    sys.exit(0)

path = Path(".github/workflows/ci.yml")
try:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
except yaml.YAMLError as e:
    print("YAML НЕ РАЗБИРАЕТСЯ: %s" % e)
    sys.exit(1)

problems = []
jobs = document.get("jobs") or {}
if not jobs:
    problems.append("нет ни одной задачи (jobs)")

for name, spec in jobs.items():
    steps = spec.get("steps") or []
    if not steps:
        problems.append("%s: нет шагов" % name)
    for index, step in enumerate(steps):
        if "uses" not in step and "run" not in step:
            problems.append("%s[%d]: ни uses, ни run" % (name, index))
        if "run" in step and "name" not in step:
            problems.append("%s[%d]: шаг run без name (в логе будет нечитаемо)"
                            % (name, index))

# The actions used must be pinned to a commit SHA, not a moving tag: this repo
# has no `pull_request_target` and no secrets for untrusted code, and a floating
# tag would undo that with one upstream compromise.
for name, spec in jobs.items():
    for index, step in enumerate(spec.get("steps") or []):
        action = step.get("uses")
        if action and "@" in action:
            ref = action.rsplit("@", 1)[1]
            if len(ref) != 40 or not all(c in "0123456789abcdef" for c in ref):
                problems.append("%s[%d]: %s закреплён не по SHA" % (name, index, action))

print("задачи:", ", ".join("%s(%d шагов)" % (n, len(s.get("steps") or []))
                          for n, s in jobs.items()))
if problems:
    for problem in problems:
        print("  ПРОБЛЕМА:", problem)
    sys.exit(1)
print("CI в порядке")
