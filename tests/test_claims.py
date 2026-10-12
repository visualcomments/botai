#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the absolutist-claim filter.

The mechanism is taken from HKUDS/DeepTutor's memory consolidator guards
(Apache-2.0); see `scripts/botai_core/claims.py` for the attribution and for what
the filter does *not* claim to do.

What is checked here:

* a fluent superlative about a person is refused — "полностью освоил" on the
  strength of one passing check is exactly the failure rule 6 and §16 describe;
* the learner's own words are exempt, because a quotation is evidence rather than
  a judgement, and refusing it would lose the very thing that makes a summary
  checkable;
* the refusal names the phrase and can be acted on, mirroring DeepTutor's
  emit-time feedback, instead of being a silent drop.

Run:
    python3 tests/test_claims.py
"""

from __future__ import annotations

import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import claims  # noqa: E402

_passed = 0
_failures: list[str] = []


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(name)
        print(f"  FAIL {name}  {detail}")


def test_absolutist_russian_claims_are_caught():
    for text in (
        "Ученик полностью освоил тему.",
        "Ученик полностью усвоил материал.",
        "Он в совершенстве владеет git.",
        "Ученик глубоко понимает тему.",
        "Он эксперт в этой области.",
        "Ученик отлично знает материал.",
        "Он не нуждается в помощи.",
        "Ученик бездарен в программировании.",
        "Он ленив и не старается.",
    ):
        check("поймано: %s" % text[:44], bool(claims.banned_in(text)), text)


def test_absolutist_english_claims_are_caught():
    for text in (
        "The learner has mastered this objective.",
        "She fully understands the concept.",
        "He is an expert in Git.",
        "The student always gets this right.",
        "They never ask for help.",
    ):
        check("поймано (en): %s" % text[:44], bool(claims.banned_in(text)), text)


def test_evidence_based_statements_pass():
    for text in (
        "Ученик показал результат на двух проверках: объяснение и перенос.",
        "Обе попытки завершены верно; одна проверка была на перенос.",
        "Ученик ответил верно после подсказки уровня HINT.",
        "Состояние: practising. Доказательств: 2.",
        "The learner answered both checks correctly.",
        "Two checks passed, one of them a transfer task.",
    ):
        check("пропущено: %s" % text[:44], not claims.banned_in(text), text)


def test_quotations_are_exempt():
    """A learner's own words are evidence, not a claim about them."""
    for text in (
        "Ученик сказал: «я всегда путаю staging».",
        "Ученик написал: “I never get this right”.",
        "Из ответа ученика: «он ненавидит git».",
        "Ученик сказал: „полностью освоил»".replace("»", "“"),
        "В попытке: `always use rebase`.",
    ):
        check("цитата не блокирует: %s" % text[:40],
              not claims.banned_in(text), text)


def test_quotation_wrapping_does_not_hide_a_real_claim():
    """The exemption is per-quotation, not per-document."""
    text = "Ученик сказал: «я путаю staging». Он полностью освоил тему."
    found = claims.banned_in(text)
    check("вывод вне цитаты пойман, хотя цитата есть",
          "полностью освоил" in found, str(found))


def test_check_text_raises_with_a_usable_code():
    try:
        claims.check_text("Ученик полностью освоил тему.")
    except claims.ClaimRejected as e:
        check("код назван", e.code == "ABSOLUTE_CLAIM_REJECTED", e.code)
        check("фраза названа", "полностью освоил" in e.phrases, str(e.phrases))
        note = claims.rejection_note(e)
        check("сообщение пригодно для модели",
              "Перепишите" in note["message_ru"], note["message_ru"][:80])
        check("сказано про цитаты",
              "цитаты" in note["note_ru"].lower(), note["note_ru"][:80])
    else:
        check("check_text поднял исключение", False, "не поднял")


def test_check_text_passes_clean_text():
    check("чистый текст проходит",
          claims.check_text("Две проверки пройдены.") is True)


def test_check_document_names_the_field():
    document = {"objective_id": "explain-diff",
                "summary_ru": "Ученик полностью освоил тему.",
                "note_ru": "Две проверки."}
    try:
        claims.check_document(document, fields=("summary_ru", "note_ru"))
    except claims.ClaimRejected as e:
        check("поле названо", e.field == "summary_ru", str(e.field))
    else:
        check("check_document поднял исключение", False, "не поднял")

    check("документ без нарушений проходит",
          claims.check_document({"summary_ru": "Две проверки."},
                                fields=("summary_ru",)) is not None)


def test_sanitize_marks_instead_of_dropping():
    document = {"note_ru": "Ученик полностью освоил тему.",
                "objective_id": "explain-diff"}
    cleaned, rejected = claims.sanitize_document(document, fields=("note_ru",))
    check("нарушение перечислено", len(rejected) == 1, str(rejected))
    check("поле заменено явной пометкой",
          "снято" in cleaned["note_ru"], cleaned["note_ru"])
    check("пометка называет причину",
          "полностью освоил" in cleaned["note_ru"], cleaned["note_ru"])
    check("остальные поля не тронуты",
          cleaned["objective_id"] == "explain-diff")


def test_the_list_is_not_empty_and_covers_both_languages():
    check("список запретов не пуст", len(claims.BANNED_PHRASES) >= 30,
          str(len(claims.BANNED_PHRASES)))
    joined = " ".join(claims.BANNED_PHRASES)
    check("есть русские обороты", any("а" <= ch <= "я" for ch in joined))
    check("есть английские обороты", "mastered" in joined and "always" in joined)


def test_filter_is_a_floor_not_a_guarantee():
    """Stated as a test so the limitation cannot quietly become a claim.

    The filter detects phrasing. A false statement that avoids every banned word
    passes, and that is a known limit rather than an oversight.
    """
    clean_lie = "Ученик справится с любой задачей по этой теме."
    check("фраза без запретных слов проходит",
          not claims.banned_in(clean_lie), clean_lie)
    check("в докстроке модуля это названо пределом, а не гарантией",
          "floor" in (claims.__doc__ or "").lower(), "проверьте claims.py")


def main():
    tests = [
        test_absolutist_russian_claims_are_caught,
        test_absolutist_english_claims_are_caught,
        test_evidence_based_statements_pass,
        test_quotations_are_exempt,
        test_quotation_wrapping_does_not_hide_a_real_claim,
        test_check_text_raises_with_a_usable_code,
        test_check_text_passes_clean_text,
        test_check_document_names_the_field,
        test_sanitize_marks_instead_of_dropping,
        test_the_list_is_not_empty_and_covers_both_languages,
        test_filter_is_a_floor_not_a_guarantee,
    ]
    for test in tests:
        print("== %s ==" % test.__name__)
        try:
            test()
        except Exception as e:  # noqa: BLE001
            import traceback
            _failures.append(test.__name__)
            print("  FAIL %s выбросил %s: %s" % (test.__name__, type(e).__name__, e))
            traceback.print_exc()

    print()
    print("%d passed, %d failed" % (_passed, len(_failures)))
    if _failures:
        for name in _failures:
            print("  - %s" % name)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
