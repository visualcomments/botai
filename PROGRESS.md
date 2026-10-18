# Harness Progress Log

## Current State

**Last Updated:** 2026-10-18 15:15
**Session ID:** (none)
**Active Feature:** p3.context-budget - Локальный реестр контекста и бюджет сессии

## Status

### What's Done

- [x] Проект клонирован
- [x] Python 3 и Git доступны
- [x] AGENTS.md и CLAUDE.md на месте
- [x] Makefile, opencode.json, .opencode/agent/ настроены
- [x] Все CLI-команды определены в scripts/cli.py
- [x] Тесты проходят (3000+ assertions)
- [x] `make check` включает компиляцию, тесты и аудит harness

### What's In Progress

- [x] p0.baseline-verification: Воспроизводимый baseline
- [x] p0.harness-state: Машиночитаемое состояние harness
- [x] p0.session-lifecycle: Жизненный цикл сессии
- [x] p0.scenario-evals: Педагогические сценарные проверки
- [x] p1.state-viewer: Машиночитаемый просмотр состояния обучения

- [x] p1.transfer-check: Проверяемый transfer-check в учебном цикле
  - Details: CLI flow проверяет две разные попытки и запрет засчитывать explain+transfer на одной попытке
  - Evidence: `python3 scripts/evals/run.py cli-transfer-flow` — passed
  - Blockers: None

- [x] p1.contract-tests: Contract-тесты для policy-path
  - Evidence: `python3 scripts/evals/run.py` — 6 scenarios passed

- [x] p2.next-slice: Отчёт о блокировках обучения
  - Details: evidence-backed блокировки и повторяющиеся затруднения без оценки
  - Evidence: `python3 scripts/evals/run.py` — 7 passed; `make check` — passed
  - Blockers: None

- [x] p3.context-budget: Локальный реестр контекста и бюджет сессии
  - Details: progressive disclosure tiers metadata/instructions/resources, без transcript и модели
  - Evidence: `python3 scripts/evals/run.py` — 8 passed; `make check` — passed
  - Blockers: None

- [x] p4.session-step-budget: Принудительный лимит шагов сессии
  - Details: предупреждение на 18 и мягкая остановка на 24 шагах
  - Evidence: `python3 scripts/evals/run.py` — 9 passed; `make check` — passed
  - Blockers: None

- [x] p4.design-document: Дизайн-документ harness-improvements
  - Evidence: `docs/harness-improvements.md` создан

- [x] p4.tests-investigation: Расследование tests/ checkout
  - Evidence: 35 test files tracked in git; tests/ directory present in checkout

### What's Next

1. Выбрать следующий P2 вертикальный срез
2. Добавить отдельный тестовый файл при восстановлении полного tests/ checkout
3. Запустить `make check` перед следующей сессией

## Blockers / Risks

- [ ] Тесты не включены в клонированную копию (ожидается в CI, но не в локальной рабочей копии)

## Decisions Made

- **Root instruction file короткий, маршрутизирующий**: AGENTS.md остается политикой; детали уходят в docs/agent/
- **features/feature_list.json как source of truth**: одна активная функция за раз
- **init.sh как стандартный bootstrap**: не требует аргументов, проверяет doctor + compile + tests

## Files Modified This Session

- `Makefile` — добавлены `check` и `harness-audit` цели
- `init.sh` — новый bootstrap-скрипт
- `features/feature_list.json` — машиночитаемый реестр функций P0
- `PROGRESS.md` — этот файл
- `SESSION_HANDOFF.md` — новый handoff

## Evidence of Completion

- [x] Tests pass: `make check` — passed
- [x] Type check clean: `python3 -m compileall -q scripts` — passed
- [x] Manual verification: `./init.sh` — passed
- [x] Pedagogical evals: `python3 scripts/evals/run.py` — 6 passed
- [x] CLI transfer flow: `python3 scripts/evals/run.py cli-transfer-flow` — passed
- [x] State view: `python3 scripts/cli.py state-view --root <workspace> --course <slug> --json` — passed

## Notes for Next Session

1. CLI команда `harness-status` должна читать features/feature_list.json и показывать текущую функцию
2. CLI команда `session-bootstrap` должна выполнять init.sh + читать feature_list.json + показывать следующую цель
3. CLI команда `session-handoff` должна сохранять JSON с active_feature, next_step, blockers
4. Проверка: `make check` должна проходить без ошибок