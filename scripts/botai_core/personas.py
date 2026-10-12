# -*- coding: utf-8 -*-
"""Presentation styles and personal badges — neither of which changes the rules.

A persona changes how the assistant sounds. It must not change what a learner is
allowed to do, what counts as knowing something, or how much help a graded task
receives. That is enforced by the schema rather than by a sentence here: a
persona file cannot carry `tools`, `permissions`, `system_prompt_override`,
`secret_paths`, `scoring_rules` or any grading policy, because the schema sets
`additionalProperties: false`. A style file that could hand itself more
authority would make every other guarantee in this project conditional on
somebody not editing a JSON file.

The same reasoning runs through the achievement reducer. Badges are:

* **off by default**, and only personal — there is no leaderboard, no daily
  streak, no penalty for a break, and no `public` visibility value at all;
* **derived from recorded events**, never from a model's account of progress;
* **never part of a course grade**, and never awarded for activity: message
  counts, session length and commit counts are explicitly not evidence here.

A revoked badge is explained and is not a punishment: the evidence behind it
turned out to be wrong, and saying so is more honest than a quiet removal.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import schemas

SCHEMA_VERSION = 2

DEFAULT_PERSONA = "neutral"

# Fields that would make a style file into a policy file. Listed explicitly so
# the refusal carries a readable reason rather than only a schema error.
FORBIDDEN_PERSONA_FIELDS = (
    "tools", "permissions", "system_prompt_override", "prompt_override",
    "secret_paths", "scoring_rules", "grading_policy", "assessment_rules",
    "assistance_ceiling", "role", "env", "environment", "commands",
    "shell", "exec", "hooks", "mcp", "model", "provider",
)

VERBOSITY = ("terse", "normal", "detailed")

# Achievement rules, with the evidence each one requires. Deterministic: every
# rule is a function of recorded events, so the same history always yields the
# same badges and a restart awards nothing twice.
RULE_VERSION = "v2-achievements-1"

ACHIEVEMENT_RULES = {
    "first-explain-back": {
        "title_ru": "Объяснил своими словами",
        "reason_ru": "Первая зачтённая проверка вида «объяснение» по реальной попытке.",
        "requires": ("explain_pass",),
    },
    "evidence-checker": {
        "title_ru": "Проверил источник",
        "reason_ru": "Ученик обнаружил ошибку в координатах или источнике и объяснил проверку.",
        "requires": ("citation_error_caught",),
    },
    "environment-understood": {
        "title_ru": "Понимает свою среду",
        "reason_ru": "Среда готова, и ученик объяснил, как её запускать и останавливать.",
        "requires": ("environment_ready", "environment_explained"),
    },
    "thoughtful-contributor": {
        "title_ru": "Аккуратный вклад",
        "reason_ru": "Полный самостоятельный просмотр изменения и проверенный локальный "
                     "пакет — независимо от того, опубликован он публично или нет.",
        "requires": ("contribution_drafted", "contribution_checked"),
    },
    "revision-learned": {
        "title_ru": "Учёл замечание",
        "reason_ru": "Ученик исправил замечание и объяснил причину; есть две связанные попытки.",
        "requires": ("revision_made", "revision_explained"),
    },
}

# Things a badge must never be awarded for. Kept as a list because these are the
# tempting proxies a gamification layer reaches for first.
NEVER_AWARD_FOR = (
    "количество сообщений",
    "длительность занятия",
    "число коммитов",
    "помощь без проверки понимания",
    "принятие PR любой ценой",
    "ежедневная серия входов",
    "публичность сдачи",
)


class PersonaError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def personas_dir(root=None):
    """Where shipped personas live.

    Resolved from this file rather than the working directory: the CLI runs from
    various places, and a persona directory that depends on the current
    directory is one that silently goes missing.
    """
    if root:
        candidate = Path(root) / "personas"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent.parent / "personas"


def _reject_policy_fields(document):
    """Refuse a persona that tries to carry authority rather than style."""
    present = [name for name in FORBIDDEN_PERSONA_FIELDS if name in document]
    if present:
        raise PersonaError(
            "PERSONA_NOT_STYLE_ONLY",
            "персона не может содержать поля, влияющие на права или правила: %s. "
            "Личность — это оформление: она не меняет доступные инструменты, "
            "задания, источники, правила помощи и оценивание."
            % ", ".join(present),
        )


def load_persona(path, *, validate=True):
    """Load and validate one persona file."""
    path = Path(path)
    if not path.is_file():
        raise PersonaError("PERSONA_MISSING", "персона не найдена: %s" % path)
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise PersonaError("PERSONA_UNREADABLE", "не удалось прочитать %s: %s" % (path, e))

    _reject_policy_fields(document)
    if validate:
        try:
            schemas.validate(document, "persona")
        except schemas.SchemaError as e:
            raise PersonaError(e.code, e.message)
    return document


def available_personas(root=None):
    """Every loadable persona, by id. A broken file is reported, not skipped."""
    directory = personas_dir(root)
    found = {}
    problems = []
    if not directory.is_dir():
        return found, problems

    for path in sorted(directory.glob("*.json")):
        try:
            document = load_persona(path)
        except PersonaError as e:
            problems.append({"path": str(path), "code": e.code, "message_ru": e.message})
            continue
        found[document["persona_id"]] = document
    return found, problems


def get_persona(persona_id, root=None):
    """One persona by id, falling back to neutral with a stated reason."""
    personas, _problems = available_personas(root)
    if persona_id in personas:
        return personas[persona_id], None
    fallback = personas.get(DEFAULT_PERSONA)
    if fallback is None:
        raise PersonaError(
            "PERSONA_UNAVAILABLE",
            "персона %r не найдена, а нейтральная недоступна: проверьте каталог personas/"
            % persona_id,
        )
    return fallback, (
        "персона %r не найдена; используется нейтральная" % persona_id
    )


def resolve(persona_id, *, low_stimulus=False, gamification=False, root=None):
    """The presentation settings in force, with neutral and quiet overrides.

    `low_stimulus` disables role insertions whatever persona is selected, and a
    disabled gamification setting hides badges. Both are the learner's switches,
    and neither discards progress: turning them on or off changes only what is
    displayed.
    """
    persona, warning = get_persona(persona_id or DEFAULT_PERSONA, root=root)

    effective = dict(persona)
    notes = []
    if warning:
        notes.append(warning)

    if low_stimulus:
        # Neutral is the absence of framing, not another frame.
        neutral, _ = get_persona(DEFAULT_PERSONA, root=root)
        effective["persona_id"] = neutral["persona_id"]
        effective["title"] = neutral["title"]
        effective["framing"] = neutral["framing"]
        effective["permitted_metaphors"] = []
        effective["tone"] = neutral["tone"]
        notes.append("включён режим без стимуляции: ролевые вставки отключены")

    effective["gamification"] = bool(gamification)
    effective["low_stimulus"] = bool(low_stimulus)
    effective["notes_ru"] = notes
    # The identity statement is fixed, not per-persona: the assistant is an AI
    # whichever style is chosen, and a persona cannot claim otherwise.
    effective["is_ai"] = True
    return effective


def validate_persona_stays_stylistic(document, *, baseline_ceiling=None):
    """Check that a persona cannot be read as relaxing a pedagogical rule.

    The schema already refuses policy fields. This catches the subtler case: an
    `examples` entry whose wording hands out what the assistance ladder forbids.
    It is a textual check with a narrow vocabulary, so it reports what it found
    rather than claiming to prove the persona safe.
    """
    problems = []

    forbidden_claims = (
        "полный ответ на оцениваем", "готовое решение оцениваем",
        "выдам решение", "дам готовый ответ",
        "я реальный преподаватель", "я профессор", "я настоящий",
        "гарантирую оценку", "поставлю зачёт",
    )
    for index, example in enumerate(document.get("examples") or []):
        text = (example.get("response_ru") or "").lower()
        for phrase in forbidden_claims:
            if phrase in text:
                problems.append({
                    "where": "examples[%d].response_ru" % index,
                    "problem_ru": "пример обещает то, что политика запрещает: %r" % phrase,
                })

    declared = [p.lower() for p in (document.get("forbidden_patterns") or [])]
    required_topics = ("оценива", "оценк", "готов")
    for topic in required_topics:
        if not any(topic in pattern for pattern in declared):
            problems.append({
                "where": "forbidden_patterns",
                "problem_ru": "не назван запрет на выдачу готового решения "
                              "оцениваемого задания",
            })
            break

    return problems


# --------------------------------------------------------------------------
# Achievements
# --------------------------------------------------------------------------

def feedback_events(*, checks=(), attempts=(), citations=(), environment=(),
                    contribution=None):
    """Reduce recorded entities into the facts the rules read, with their evidence.

    Returns a dict of `fact -> sorted entity ids that established it`. Only
    recorded things count: "the model said the learner understood" is not an
    input, and neither is a session length. Carrying the ids is what lets a
    badge point at the checks it rests on instead of at the name of a rule.
    """
    facts: dict[str, set[str]] = {}

    def record(fact, entity_id):
        if entity_id:
            facts.setdefault(fact, set()).add(str(entity_id))

    attempt_ids = {a.get("attempt_id") for a in attempts}
    for check in checks:
        if check.get("verdict") != "pass":
            continue
        # A pass must point at a real attempt: a check with no attempt is a
        # statement about nothing, and a badge built on it would be too.
        if check.get("attempt_id") not in attempt_ids:
            continue
        if check.get("kind") in ("explain", "critique"):
            record("explain_pass", check.get("check_id"))

    for citation in citations:
        if citation.get("error_found") and citation.get("explained"):
            record("citation_error_caught", citation.get("event_id"))

    for status in environment:
        if status.get("status") == "READY":
            record("environment_ready", status.get("operation_id") or status.get("event_id"))
        if status.get("explained_by_learner"):
            record("environment_explained",
                   status.get("explanation_event_id") or status.get("event_id"))

    if contribution:
        if contribution.get("self_reviewed"):
            record("contribution_drafted", contribution.get("contribution_id"))
        if contribution.get("checks"):
            record("contribution_checked", contribution.get("contribution_id"))
        if contribution.get("assessment_implemented"):
            record("revision_made", contribution.get("revision_event_id")
                   or contribution.get("contribution_id"))
        if contribution.get("assessment_implemented") and contribution.get("revision_explained"):
            record("revision_explained", contribution.get("revision_event_id")
                   or contribution.get("contribution_id"))

    # A rule whose evidence is a fact with no id cannot be awarded: the schema
    # requires evidence, and an invented id would be worse than no badge.
    return {fact: sorted(ids) for fact, ids in facts.items() if ids}


def evaluate_achievements(facts, *, existing=(), learner_id, course_id,
                          rule_version=RULE_VERSION, awarded_at):
    """Which badges the recorded facts now support.

    `facts` maps a fact name to the entity ids that established it. Returns
    `(to_award, to_revoke, satisfied)`. Existing awards are compared by
    `(learner, course, achievement, rule_version)`: re-running awards nothing new,
    and revoking is proposed only when the evidence behind a badge is no longer
    present.
    """
    satisfied = []
    for achievement_id, rule in sorted(ACHIEVEMENT_RULES.items()):
        if all(required in facts for required in rule["requires"]):
            satisfied.append(achievement_id)

    held = {a.get("achievement_id") for a in existing if not a.get("revoked_at")}

    to_award = []
    for achievement_id in satisfied:
        if achievement_id in held:
            continue
        rule = ACHIEVEMENT_RULES[achievement_id]
        evidence = _evidence_for(achievement_id, facts)
        if not evidence:
            # No entity ids means nothing to point at. Awarding anyway would
            # produce a badge whose justification cannot be re-checked.
            continue
        to_award.append({
            "schema_version": SCHEMA_VERSION,
            "achievement_id": achievement_id,
            "rule_version": rule_version,
            "learner_id": learner_id,
            "course_id": course_id,
            "evidence_ids": evidence,
            "awarded_at": awarded_at,
            "revoked_at": None,
            "revocation_reason_ru": None,
            "visibility": "learner_visible",
            "title_ru": rule["title_ru"],
            "reason_ru": rule["reason_ru"],
        })

    to_revoke = []
    for award in existing:
        if award.get("revoked_at"):
            continue
        if award.get("achievement_id") not in satisfied:
            to_revoke.append(award)

    return to_award, to_revoke, satisfied


def _evidence_for(achievement_id, facts):
    """Every entity id the rule's own facts rest on, deduplicated and sorted.

    A badge for a rule needing two facts lists the evidence of both: pointing at
    only one would understate what the badge claims to rest on, and a reader
    could not tell which half was missing if it were later withdrawn.
    """
    collected = set()
    for required in ACHIEVEMENT_RULES[achievement_id]["requires"]:
        collected.update(facts.get(required) or ())
    return sorted(str(e) for e in collected)


def revoke(award, *, reason_ru, revoked_at):
    """Withdraw a badge, keeping the record and explaining why.

    The award is not deleted: a badge that disappears without trace makes the
    log untrustworthy, and the learner is owed the reason. This is not a
    penalty, and nothing else about the learner's record changes.
    """
    if not reason_ru or not reason_ru.strip():
        raise PersonaError(
            "REVOCATION_REASON_REQUIRED",
            "отзыв награды требует объяснения: молчаливое снятие нельзя ни "
            "понять, ни проверить",
        )
    updated = dict(award)
    updated["revoked_at"] = revoked_at
    updated["revocation_reason_ru"] = reason_ru[:500]
    return updated


def render_achievements(awards, *, persona=None, gamification=False):
    """How badges are shown. Empty when the learner has not opted in."""
    if not gamification:
        return {
            "shown": False,
            "reason_ru": "награды выключены: включение — отдельный выбор ученика",
            "items": [],
        }

    items = []
    for award in awards:
        if award.get("revoked_at"):
            continue
        items.append({
            "achievement_id": award["achievement_id"],
            "title_ru": award.get("title_ru"),
            "reason_ru": award.get("reason_ru"),
            "awarded_at": award.get("awarded_at"),
        })

    return {
        "shown": True,
        "reason_ru": "личные значки; общей таблицы, серий и штрафов за перерыв нет",
        "items": items,
        "excluded_from_grade_ru": "награды не входят в оценку курса",
    }


def ensure_no_competitive_reward(document):
    """Refuse a settings document that asks for a comparative or pressuring feature.

    `public`, leaderboards and streaks are not offered anywhere in 2.0. Accepting
    the field and ignoring it would let a caller believe the feature exists.
    """
    forbidden = {
        "leaderboard": "общая таблица лидеров",
        "public_badges": "публичные значки",
        "streak": "серия ежедневных входов",
        "streak_days": "серия ежедневных входов",
        "penalty": "штраф за перерыв",
        "rank": "рейтинг среди учеников",
        "compare_with_others": "сравнение с другими учениками",
    }
    present = [name for name in forbidden if name in document]
    if present:
        raise PersonaError(
            "COMPETITIVE_REWARD_REFUSED",
            "такие возможности в 2.0 не предусмотрены: %s. Награды здесь личные "
            "и не сравнивают учеников."
            % ", ".join(forbidden[name] for name in present),
        )
    return True
