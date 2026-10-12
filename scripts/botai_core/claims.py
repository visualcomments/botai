# -*- coding: utf-8 -*-
"""Reject absolutist claims about a learner before they are written down.

Where this comes from
---------------------
The idea is taken from HKUDS/DeepTutor, `deeptutor/services/memory/consolidator/
guards.py` (Apache-2.0), which keeps a `BANNED_PHRASES` list and drops any
synthesis whose text contains one of them. Three of their decisions are the
reason this module exists in that shape:

* **The guard runs at emit time**, not after the fact. Their comment says the
  earlier design filtered the model's output once it had already returned, "which
  meant rejection was a silent drop". A refusal the writer never sees is a
  refusal that turns into a lost fact.
* **Quoted regions are exempt.** If the learner themselves said "I always mix up
  staging", that is a quotation, not a judgement about a person.
* **A post-hoc pass remains as a safety net**, not as the mechanism.

Why botai needs it
------------------
The policy's rule 6 forbids invented conclusions, and §16 forbids claims about a
person that no evidence supports — but until now there was no mechanism, only a
sentence. `demonstrated` was already protected (it needs two checks, one of them
a transfer), yet a *free-text summary* could still say "полностью освоил" on the
strength of one `pass`. The same principle that lowers a legacy `mastered` claim
to `practising` applies here: a claim about a person needs evidence, and a
superlative is a claim.

Scope, stated honestly: this detects phrasing, not falsehood. A summary can be
wrong without using any banned word, and a banned word can appear in a sentence
that is true and evidence-backed. So this is a **floor**, not a correctness
guarantee — it stops the specific failure of a fluent superlative standing in
for evidence, and nothing more.
"""

from __future__ import annotations

import re

# Phrases that assert something about a person more strongly than any evidence a
# course can produce. Both languages are listed because the harness writes
# Russian while models insert English absolutes.
BANNED_PHRASES = (
    # Russian: completed mastery and depth
    "полностью освоил", "полностью усвоил", "полностью понял", "полностью понимает",
    "полностью разобрался", "в совершенстве", "безупречно", "идеально понял",
    "досконально", "глубоко понимает", "глубоко освоил",
    "эксперт", "эксперт в", "виртуозно", "мастерски",
    # Russian: knowledge of a mind
    "всегда", "никогда", "абсолютно всегда",
    "отлично знает", "прекрасно знает", "знает всё", "не нуждается в помощи",
    "интуитивно чувствует", "легко справится с любым",
    # Russian: character and feeling
    "любит", "обожает", "ненавидит", "страстно увлечён", "прирождённый",
    "талантливый", "одарённый", "неспособен", "бездарен", "ленив", "безнадёжен",
    # English absolutes, as DeepTutor lists them
    "deeply", "truly", "mastered", "expert in", "passionate",
    "loves", "hates", "always", "never", "fully understands",
    "perfectly understands", "flawless", "effortlessly",
)

# Text inside these is a quotation and is exempt from the check. Ruby quotes are
# DeepTutor's convention; the rest are what Russian prose actually uses.
_QUOTED_RE = re.compile(
    r"«[^»]*»"          # «…»
    r"|“[^”]*”"          # “…”
    r"|„[^“]*“"          # „…“
    r"|\"[^\"]*\""       # "…"
    r"|`[^`]*`"          # `…`
    r"|「[^」]*」"          # 「…」
)


class ClaimRejected(RuntimeError):
    """A phrase was refused. Carries the offending phrases, never the text."""

    def __init__(self, phrases, *, field=None):
        super().__init__("абсолютное суждение: %s" % ", ".join(phrases))
        self.code = "ABSOLUTE_CLAIM_REJECTED"
        self.phrases = tuple(phrases)
        self.field = field


def banned_in(text):
    """Phrases present outside every quotation, or an empty tuple.

    Quotations are removed first, so a learner's own words never trip the check.
    """
    if not text:
        return ()
    stripped = _QUOTED_RE.sub(" ", str(text)).lower()
    found = []
    for phrase in BANNED_PHRASES:
        if phrase in stripped:
            found.append(phrase)
    return tuple(found)


def check_text(text, *, field=None):
    """Raise `ClaimRejected` if the text makes an absolutist claim."""
    found = banned_in(text)
    if found:
        raise ClaimRejected(found, field=field)
    return True


def check_document(document, *, fields=None):
    """Check the named string fields of a document, naming the first offender.

    Returns the document unchanged on success so it can be used inline:
    `document = check_document(document, fields=("summary_ru", "note_ru"))`.
    """
    for name in (fields or ()):
        value = document.get(name)
        if isinstance(value, str):
            found = banned_in(value)
            if found:
                raise ClaimRejected(found, field=name)
    return document


def sanitize_document(document, *, fields=None):
    """Replace offending fields with an explicit marker instead of dropping them.

    Used where refusing outright would lose a record that is otherwise correct —
    a teacher's note, for instance. The marker is deliberately ugly: a reader
    must see that something was removed, and the original stays available to the
    caller in `rejected` so a human can decide.
    """
    rejected = []
    for name in (fields or ()):
        value = document.get(name)
        if not isinstance(value, str):
            continue
        found = banned_in(value)
        if not found:
            continue
        rejected.append({"field": name, "phrases": list(found)})
        document[name] = (
            "[снято: абсолютное суждение без доказательства — %s]"
            % ", ".join(found)
        )
    return document, rejected


def rejection_note(error):
    """A message a model can act on, mirroring DeepTutor's emit-time feedback."""
    return {
        "code": error.code,
        "message_ru": (
            "Формулировка утверждает о человеке больше, чем подтверждают "
            "доказательства: %s. Перепишите, опираясь на то, что ученик "
            "показал, а не на оценку его способностей."
            % ", ".join(error.phrases)
        ),
        "phrases": list(error.phrases),
        "field": error.field,
        "note_ru": (
            "Цитаты в «…», „…“, \"…\", `…` и 「…」 не проверяются: слова самого "
            "ученика не являются выводом о нём."
        ),
    }
