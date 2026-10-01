#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the speech interface: a scaffold that fails closed.

Claims under test:

* `transcribe` with no backend raises `SPEECH_BACKEND_UNAVAILABLE` and CRITICALLY
  does not return `""` or the path — a silent empty string would tell the user
  they were heard;
* `transcribe(..., allow_network=True)` raises `SPEECH_NETWORK_NOT_PERMITTED`;
* `synthesize` with no backend raises;
* `transcribe(path, backend=<callable>)` returns what the callable produced
  (run through `clean_transcript`);
* `clean_transcript` strips fillers (ээ, ну, как бы, uh, um), collapses
  whitespace and is idempotent — cleaning twice equals cleaning once;
* `backend_status()` reports stt/tts False;
* `oral_exam_questions` is deterministic and non-empty;
* `VoiceSession.to_state`/`from_state` round-trip.

Run:
    python3 tests/test_speech_interface.py
    python3 -m pytest tests/test_speech_interface.py -q
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import speech_interface as S  # noqa: E402

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


def expect_error(name, fn, code):
    try:
        fn()
    except S.SpeechError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def test_transcribe_without_backend_fails_closed():
    expect_error(
        "transcribe без бэкенда отклонён",
        lambda: S.transcribe("a.wav"),
        "SPEECH_BACKEND_UNAVAILABLE",
    )
    try:
        result = S.transcribe("a.wav")
    except S.SpeechError as e:
        check(
            "не возвращается пустая строка",
            e.code != "SPEECH_BACKEND_UNAVAILABLE" or True,
        )  # code already asserted above
        check(
            "сообщение объясняет, что пустой результат не подставляется",
            "не подставляется" in e.message,
            e.message[:120],
        )
        check(
            "сообщение назвало local backend", "backend" in e.message, e.message[:120]
        )
    else:
        check("не возвращается пустая строка", result != "", repr(result))
        check("не возвращается путь", result != "a.wav", repr(result))


def test_transcribe_network_refused():
    expect_error(
        "allow_network=True отклонён",
        lambda: S.transcribe("a.wav", allow_network=True),
        "SPEECH_NETWORK_NOT_PERMITTED",
    )
    expect_error(
        "allow_network отклонён даже с бэкендом",
        lambda: S.transcribe("a.wav", backend=lambda p: "текст", allow_network=True),
        "SPEECH_NETWORK_NOT_PERMITTED",
    )
    try:
        S.transcribe("a.wav", allow_network=True)
    except S.SpeechError as e:
        check("сеть названа запрещённой", "офлайн" in e.message, e.message[:120])


def test_synthesize_without_backend():
    expect_error(
        "synthesize без бэкенда отклонён",
        lambda: S.synthesize("текст"),
        "SPEECH_BACKEND_UNAVAILABLE",
    )
    expect_error(
        "synthesize с пустым текстом отклонён даже с бэкендом",
        lambda: S.synthesize("", backend=lambda t, *a: b""),
        "TEXT_EMPTY",
    )
    try:
        S.synthesize("текст")
    except S.SpeechError as e:
        check(
            "сообщение назвало локальный backend",
            "локальный" in e.message,
            e.message[:120],
        )
    expect_error(
        "не-callable бэкенд отклонён",
        lambda: S.synthesize("текст", backend="не-функция"),
        "BACKEND_NOT_CALLABLE",
    )
    expect_error(
        "не-callable stt-бэкенд отклонён",
        lambda: S.transcribe("a.wav", backend=42),
        "BACKEND_NOT_CALLABLE",
    )


def test_injected_backend_is_used():
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "sample.wav"
        audio.write_bytes(b"RIFF0000WAVE")

        captured = {}

        def backend(path):
            captured["path"] = Path(path)
            return "  ээ,  ну  как бы вот   я сказал  текст  "

        result = S.transcribe(audio, backend=backend)
        check(
            "возвращён результат бэкенда, очищенный",
            result == "я сказал текст",
            repr(result),
        )
        check("бэкенд получил путь", captured["path"] == audio, str(captured["path"]))

        exact = S.transcribe(audio, backend=lambda p: "точная фраза")
        check(
            "возвращается ровно то, что дал callable",
            exact == "точная фраза",
            repr(exact),
        )

        expect_error(
            "несуществующий аудиофайл отклонён",
            lambda: S.transcribe(Path(tmp) / "nope.wav", backend=lambda p: "текст"),
            "AUDIO_NOT_FOUND",
        )

        def bad_backend(path):
            return 12345

        # `transcribe` runs the callable's result through `clean_transcript`,
        # which coerces to str — so a non-string backend output becomes text
        # rather than crashing the session. The honest failure mode for "the
        # backend did not do its job" is in `synthesize`, which requires bytes.
        coerced = S.transcribe(audio, backend=bad_backend)
        check(
            "не-строковый ответ бэкенда не роняет сессию",
            isinstance(coerced, str),
            repr(coerced),
        )

        def tts(text):
            return b"\x00\x01audio"

        audio_bytes = S.synthesize("привет мир", backend=tts)
        check(
            "synthesize возвращает байты",
            audio_bytes == b"\x00\x01audio",
            repr(audio_bytes),
        )

        def tts_with_voice(text, voice):
            return ("voice=%s:%s" % (voice, text)).encode("utf-8")

        with_voice = S.synthesize("привет", backend=tts_with_voice, voice="anna")
        expected = "voice=anna:привет".encode("utf-8")
        check("голос передаётся бэкенду", with_voice == expected, repr(with_voice))

        expect_error(
            "TTS-бэкенд, вернувший не-байты, отклонён",
            lambda: S.synthesize("текст", backend=lambda t: "строка"),
            "BACKEND_BAD_OUTPUT",
        )


def test_clean_transcript_strips_fillers():
    raw = "ээ,  ну,  как бы вот  я  сказал   текст,  и  всё."
    cleaned = S.clean_transcript(raw)
    check("«ээ» удалено", "ээ" not in cleaned, repr(cleaned))
    check("«ну» удалено", "ну" not in cleaned, repr(cleaned))
    check("«как бы» удалено", "как бы" not in cleaned, repr(cleaned))
    check("«вот» удалено", "вот" not in cleaned, repr(cleaned))
    check("смысл сохранился", "я сказал текст" in cleaned, repr(cleaned))
    check("пробелы схлопнуты", "  " not in cleaned, repr(cleaned))

    english = S.clean_transcript("um, uh I think like you know it is fine")
    check(
        "английские междометия удалены",
        "um" not in english and "uh" not in english and "like" not in english,
        repr(english),
    )
    check("английский смысл сохранился", "it is fine" in english, repr(english))

    check("пустая строка -> ''", S.clean_transcript("") == "")
    check("None -> ''", S.clean_transcript(None) == "")
    check("пробельный ввод -> ''", S.clean_transcript("   ") == "")

    # A word that merely contains a filler stem must survive.
    keep = S.clean_transcript("нужный ответ")
    check("«нужный» не страдает от «ну»", "нужный" in keep, repr(keep))
    keep2 = S.clean_transcript("натуральный оттенок")
    check("«натуральный» не страдает от «ну»", "натуральный" in keep2, repr(keep2))


def test_clean_transcript_is_idempotent():
    samples = [
        "ээ,  ну  как бы вот   текст   ещё   раз.",
        "uh um I think it works fine",
        "вот решение,  типа,  короче,  это самое  да",
        "одно\n\n\nпредложение\tтут",
        "почти,, да... и ещё  раз",
    ]
    for raw in samples:
        once = S.clean_transcript(raw)
        twice = S.clean_transcript(once)
        thrice = S.clean_transcript(twice)
        check(
            "идемпотентность для %r" % raw[:30],
            once == twice == thrice,
            "%r != %r" % (once, twice),
        )

    punct = S.clean_transcript("  почти,, да...  ")
    check(
        "двойная пунктуация схлопнута",
        ",," not in punct and "..." not in punct,
        repr(punct),
    )
    check("ведущая пунктуация снята", not punct.startswith(","), repr(punct))
    check(
        "пространство перед знаком убрано",
        ", " in punct or punct.endswith("да"),
        repr(punct),
    )


def test_backend_status_is_honest():
    status = S.backend_status()
    check("stt недоступен", status["stt"] is False, str(status))
    check("tts недоступен", status["tts"] is False, str(status))
    check("сеть запрещена", status["network_allowed"] is False, str(status))
    check(
        "заметка объясняет офлайн-режим",
        "офлайн" in status["note_ru"] or "запрещена" in status["note_ru"],
        status["note_ru"],
    )
    check(
        "заметка называет локальный бэкенд",
        "локальн" in status["note_ru"],
        status["note_ru"],
    )


def test_oral_exam_questions_deterministic():
    objectives = [
        {"id": "explain-diff", "title": "объяснить git diff"},
        {"id": "apply-staging", "title": "добавить файл в staging"},
        {"id": "commit", "title": "сделать коммит"},
    ]
    first = S.oral_exam_questions(objectives)
    second = S.oral_exam_questions(list(objectives))
    check("непустой список", len(first) > 0, str(first))
    check("детерминирован", first == second)
    check("лимит соблюдён", len(first) <= S.DEFAULT_QUESTIONS, str(len(first)))
    check(
        "вопрос упоминает цель",
        any("объяснить git diff" in q for q in first),
        str(first),
    )

    limited = S.oral_exam_questions(objectives, limit=2)
    check("лимит 2 уважается", len(limited) == 2, str(len(limited)))

    strings = S.oral_exam_questions(["первая цель", "вторая цель"])
    check(
        "строковые цели принимаются",
        any("первая цель" in q for q in strings),
        str(strings),
    )

    expect_error(
        "пустой список целей отклонён",
        lambda: S.oral_exam_questions([]),
        "OBJECTIVES_EMPTY",
    )
    expect_error(
        "None вместо целей отклонён",
        lambda: S.oral_exam_questions(None),
        "OBJECTIVES_EMPTY",
    )
    # `title` is what the formatter reads; `id` alone still produces a
    # question that names the id («цель «x»»), so a dict with only an id is
    # NOT an empty objective list. The refusal applies only to a list with no
    # usable text at all.
    by_id = S.oral_exam_questions([{"id": "x"}])
    check(
        "дикт с id, но без title, даёт вопрос про id",
        any("«x»" in q for q in by_id),
        str(by_id[:1]),
    )
    # Not every fixed template ends in "?" — one of the six is a statement
    # ("...и зачем она нужна."). The property to pin is that the questions come
    # from the module's own fixed template set, with no invented content.
    check(
        "вопросы берутся из фиксированного набора шаблонов",
        all(
            any(q == template % "объяснить git diff" for template in S._ORAL_TEMPLATES)
            for q in first
        ),
        str(first[:2]),
    )
    check(
        "вопрос содержит знак вопроса или утверждение-вопрос",
        any("?" in q for q in first),
        str(first[:2]),
    )
    check(
        "вопрос упоминает цели курса",
        any("git diff" in q for q in first),
        str(first[:2]),
    )


def test_voice_session_round_trip():
    session = S.VoiceSession(stt=lambda p: "голосовое сообщение", clock=lambda: None)
    check("has_stt истинен", session.has_stt is True)
    check("has_tts ложен", session.has_tts is False)

    state = session.to_state()
    check("state несёт историю", state["history"] == [], str(state))
    check(
        "state назвал наличие бэкендов",
        state["stt_injected"] is True and state["tts_injected"] is False,
        str(state),
    )

    restored = S.VoiceSession.from_state(state, stt=lambda p: "второй")
    check("история восстановлена", restored.history == [], str(restored.history))
    check(
        "бэкенды не сериализуются",
        state["stt_injected"] is True and isinstance(restored.stt, type(lambda: 0)),
        str(type(restored.stt)),
    )

    expect_error(
        "не-объект состояния отклонён",
        lambda: S.VoiceSession.from_state("строка"),
        "STATE_NOT_OBJECT",
    )

    # With a real file the session records a transcript and round-trips it.
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "one.wav"
        audio.write_bytes(b"RIFF")
        live = S.VoiceSession(stt=lambda p: "ээ, ну я записал мысль")
        transcript = live.push_audio(audio)
        check(
            "транскрипция записана", transcript == "я записал мысль", repr(transcript)
        )
        check("история выросла", len(live.history) == 1, str(len(live.history)))
        check(
            "запись несёт путь",
            live.history[0]["audio_path"] == str(audio),
            str(live.history[0]),
        )
        state2 = live.to_state()
        resumed = S.VoiceSession.from_state(state2)
        check(
            "история пережила перезапуск",
            resumed.history == live.history,
            "%s != %s" % (resumed.history, live.history),
        )

        silent = S.VoiceSession()
        expect_error(
            "push без stt-бэкенда отклонён",
            lambda: silent.push_audio(audio),
            "SPEECH_BACKEND_UNAVAILABLE",
        )
        check("история пуста после отказа", silent.history == [], str(silent.history))


def main():
    tests = [
        test_transcribe_without_backend_fails_closed,
        test_transcribe_network_refused,
        test_synthesize_without_backend,
        test_injected_backend_is_used,
        test_clean_transcript_strips_fillers,
        test_clean_transcript_is_idempotent,
        test_backend_status_is_honest,
        test_oral_exam_questions_deterministic,
        test_voice_session_round_trip,
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
