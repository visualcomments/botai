# -*- coding: utf-8 -*-
"""Speech interface — a SCAFFOLD, offline only, and it fails closed (design §7.3).

The harness ships **no TTS/STT engine and no network**. That is not a missing
feature to be worked around later; it is the design's offline mode (§9.1) and
the harness's rule that nothing opens a socket. So:

* `transcribe()` and `synthesize()` accept an **injected local backend** — a
  plain callable the caller supplies (`(Path) -> str` and `(str) -> bytes`).
  With no backend, both raise `SpeechError("SPEECH_BACKEND_UNAVAILABLE")`.
* Nothing here ever returns a file path, an empty string, or a fabricated
  transcript "as if" it were a result. A silent empty string is the worst
  possible failure for a voice interface: the user believes they were heard.
* `backend_status()` reports the absence honestly so `doctor` does too.

The one piece of real logic in this module is `clean_transcript()`: a pure,
deterministic function that strips filler words, collapses whitespace and fixes
sentence spacing. It is the part that is worth testing, and it is the part that
does not need a model.

`oral_exam_questions()` is likewise a formatter: deterministic Russian prompts
built from objective titles. No LLM, no invented content — a formatting helper
that turns a list of objectives into questions a person can read aloud.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import tutoring

SCHEMA_VERSION = 2

DEFAULT_QUESTIONS = 5


class SpeechError(RuntimeError):
    """A refused speech operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Transcript cleaning — the real, testable logic
# ---------------------------------------------------------------------------

# Filler words and interjections, Russian and English. Matched as whole words
# (with optional comma/space) so "ну" is removed but "нужный" is not.
_FILLER_RU = ("ээ", "э-э", "ну", "как бы", "вот", "типа", "это самое", "короче")
_FILLER_EN = ("uh", "um", "erm", "like", "you know", "basically")

_FILLER_RE = re.compile(
    r"(?i)(?<![\w-])("
    + "|".join([re.escape(word) for word in _FILLER_RU + _FILLER_EN])
    + r")(?![\w-])"
)

# A repeated punctuation mark left behind by a filler removal ("почти,, да").
_DOUBLE_PUNCT_RE = re.compile(r"([,;:.!?])\1+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,;:.!?])")
_SPACE_AFTER_OPEN_RE = re.compile(r"([,;:.!?])\s*(?=[,;:.!?])")
_LEADING_PUNCT_RE = re.compile(r"^[,;:.!?\s]+")


def clean_transcript(text):
    """Strip fillers, collapse whitespace, fix sentence spacing. Pure.

    Returns the cleaned string; no I/O, no model, no clock. Empty or None input
    returns `""` rather than raising, because a blank utterance is a normal
    outcome for a voice channel.
    """
    if not text:
        return ""
    cleaned = str(text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _FILLER_RE.sub(" ", cleaned)
    # Newlines separate sentences in a transcript; collapse runs of spaces but
    # keep the newline as a boundary.
    cleaned = "\n".join(" ".join(line.split()) for line in cleaned.split("\n"))
    cleaned = _DOUBLE_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = _SPACE_AFTER_OPEN_RE.sub(r"\1 ", cleaned)
    cleaned = _LEADING_PUNCT_RE.sub("", cleaned)
    # Sentence spacing: ensure a single space after `.`/`!`/`?` when more
    # letters follow on the same line.
    cleaned = re.sub(r"([.!?])(?=[^\s\d])", r"\1 ", cleaned)
    return "\n".join(line.strip() for line in cleaned.split("\n")).strip()


# ---------------------------------------------------------------------------
# Backends: injected, or refused
# ---------------------------------------------------------------------------


def backend_status():
    """Honest report for `doctor`: no engine, no network."""
    return {
        "stt": False,
        "tts": False,
        "network_allowed": False,
        "note_ru": (
            "Модуль речи — заготовка: движок распознавания и синтеза не "
            "установлен, сеть запрещена. Работает только чистка транскрипта "
            "и локальный бэкенд, переданный вызывающим кодом."
        ),
    }


def transcribe(audio_path, *, backend=None, allow_network=False):
    """Transcribe `audio_path` with a LOCAL backend, or refuse.

    `backend` is a callable `(Path) -> str` supplied by the caller — a local
    Whisper, a local Coqui, anything already on the machine. With `None`, this
    raises: it never returns the path, never an empty string, never a
    placeholder "transcript". A caller that got `""` back would treat silence
    as a real utterance.

    `allow_network` is accepted and ignored by design: the parameter exists so
    a caller's intent is visible, but passing `True` does not enable anything —
    the harness has no network client to enable.
    """
    if allow_network:
        # Not an error about being *unable*; a refusal of the premise.
        raise SpeechError(
            "SPEECH_NETWORK_NOT_PERMITTED",
            "сетевой доступ не разрешён: харнесс работает офлайн и не имеет "
            "сетевого клиента. Передайте локальный backend=..., либо "
            "воспользуйтесь вводом текстом.",
        )
    if backend is None:
        raise SpeechError(
            "SPEECH_BACKEND_UNAVAILABLE",
            "движок распознавания речи не установлен: Whisper и облачный "
            "STT требуют сети, которой здесь нет. Передайте локальный "
            "backend=callable(Path) -> str из вызывающего кода. Пустой "
            "результат не подставляется — тишина не является транскриптом.",
        )
    if not callable(backend):
        raise SpeechError(
            "BACKEND_NOT_CALLABLE",
            "backend должен быть вызываемым объектом (Path) -> str, получено: %r"
            % (backend,),
        )
    path = Path(audio_path)
    if not path.is_file():
        raise SpeechError(
            "AUDIO_NOT_FOUND",
            "аудиофайл не найден: %s" % path,
        )
    return clean_transcript(backend(path))


def synthesize(text, *, backend=None, voice=None):
    """Synthesize `text` with a LOCAL backend, or refuse.

    `backend` is a callable `(str) -> bytes`. `voice` is passed through to the
    backend untouched; with no backend the call is refused, never faked.
    """
    if backend is None:
        raise SpeechError(
            "SPEECH_BACKEND_UNAVAILABLE",
            "движок синтеза речи не установлен: облачный TTS требует сети, "
            "локальный движок в харнесс не входит. Передайте локальный "
            "backend=callable(str) -> bytes из вызывающего кода.",
        )
    if not callable(backend):
        raise SpeechError(
            "BACKEND_NOT_CALLABLE",
            "backend должен быть вызываемым объектом (str) -> bytes, "
            "получено: %r" % (backend,),
        )
    if not text:
        raise SpeechError("TEXT_EMPTY", "нечего озвучивать: текст пуст")
    data = (
        backend(clean_transcript(text))
        if voice is None
        else backend(clean_transcript(text), voice)
    )
    if not isinstance(data, (bytes, bytearray)):
        raise SpeechError(
            "BACKEND_BAD_OUTPUT",
            "локальный backend вернул %s вместо байтов аудио" % type(data).__name__,
        )
    return bytes(data)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class VoiceSession:
    """A transcript history over injected local backends.

    Holds `history` (one entry per utterance) and round-trips through
    `to_state` / `from_state`, so a session survives a restart without the
    backends needing to be serialised — the caller re-injects them.
    """

    def __init__(self, *, stt=None, tts=None, clock=None):
        self.stt = stt
        self.tts = tts
        self._clock = clock
        self.history = []

    @property
    def has_stt(self):
        return callable(self.stt)

    @property
    def has_tts(self):
        return callable(self.tts)

    def push_audio(self, path):
        """Transcribe one audio file and append it to `history`.

        Raises `SpeechError` when no local STT backend was injected — the
        session never records a fabricated utterance.
        """
        transcript = transcribe(path, backend=self.stt)
        entry = {
            "schema_version": SCHEMA_VERSION,
            "at": tutoring.now_iso(self._clock),
            "audio_path": str(path),
            "transcript": transcript,
        }
        self.history.append(entry)
        return transcript

    def to_state(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "history": list(self.history),
            "stt_injected": self.has_stt,
            "tts_injected": self.has_tts,
        }

    @classmethod
    def from_state(cls, state, *, stt=None, tts=None, clock=None):
        if not isinstance(state, dict):
            raise SpeechError(
                "STATE_NOT_OBJECT", "состояние голосовой сессии должно быть объектом"
            )
        session = cls(stt=stt, tts=tts, clock=clock)
        session.history = list(state.get("history") or [])
        return session


# ---------------------------------------------------------------------------
# Oral exam prompts (formatting only)
# ---------------------------------------------------------------------------

_ORAL_TEMPLATES = (
    "Объясните своими словами, что делает цель «%s» и зачем она нужна.",
    "Как бы вы объяснили цель «%s» новичку, который её ещё не видел?",
    "В каком случае цель «%s» уже достигнута, а в каком — нет?",
    "Какое значение имеет цель «%s» для следующей темы курса?",
    "Проверьте себя: где в своей работе вы применяли цель «%s»?",
    "Что изменится, если цель «%s» не будет достигнута?",
)


def oral_exam_questions(objectives, *, limit=DEFAULT_QUESTIONS):
    """Deterministic Russian oral-exam prompts from objective titles.

    A formatter, not a generator: the questions come from a fixed template set
    and the objective titles the caller supplies. No model is involved, and no
    question invents content about an objective it has not seen. Objectives may
    be strings or dicts with `title`/`id`.
    """
    titles = []
    for item in objectives or []:
        if isinstance(item, dict):
            title = item.get("title") or item.get("id")
        else:
            title = item
        text = str(title or "").strip()
        if text:
            titles.append(text)
    if not titles:
        raise SpeechError(
            "OBJECTIVES_EMPTY",
            "нужен хотя бы один список целей с названиями",
        )

    questions = []
    for index, title in enumerate(titles):
        for template in _ORAL_TEMPLATES:
            if len(questions) >= int(limit or DEFAULT_QUESTIONS):
                return questions
            questions.append(template % title)
        if len(questions) >= int(limit or DEFAULT_QUESTIONS):
            return questions
    return questions
