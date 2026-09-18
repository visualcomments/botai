# -*- coding: utf-8 -*-
"""Scoped retrieval and quote verification.

Two failures this module exists to prevent, both of which look like success from
the outside:

* **Retrieval that crosses a boundary.** The v1 harness had one global corpus
  root, so a search could return a fragment of a *different* course — or of the
  teacher's solution notes — while looking perfectly correct. Here the scope is
  resolved to a set of files *before* any candidate is considered, and an
  excluded path never enters the candidate set, so it cannot be returned and
  then filtered.
* **A citation that is present but wrong.** A quotation can match the text and
  still point at the wrong place: the same sentence appears in two chunks, the
  line numbers are off by one, the page number is the printed one rather than
  the physical one. `verify_quote` therefore checks the text *and* the
  coordinates, and reports `mismatch` rather than quietly repairing the
  locator — silently fixing a citation destroys the evidence that the model
  produced a bad one.

What it does not claim: a string match is not truth. `support_status` is a
separate field precisely so "the author wrote this" never gets read as "this
proves the claim". Nothing here evaluates meaning, and the field defaults to
`not_checked` rather than to a guess.
"""

from __future__ import annotations

import re
import unicodedata

from . import corpus, schemas
from . import paths

MAX_LIMIT = 8
MAX_HIT_TEXT = 4000
MAX_EXCERPT = 200

# Retrieval methods, reported honestly. A text search is not a semantic one, and
# a caller that cannot tell them apart will overstate what was found.
METHODS = ("text", "chunk", "semantic")


class RetrievalError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------

def resolve_scope(course, *, material_snapshot=None):
    """The files a search may read, resolved before any query runs.

    Returns include/exclude roots plus the concrete readable file list. The
    exclusion check happens here, once, rather than at each hit — a filter
    applied after ranking is a filter that will eventually be forgotten in one
    code path.
    """
    if course is None:
        raise RetrievalError("COURSE_NOT_ACCEPTED",
                             "курс не принят: область поиска не определена")

    base = None
    if material_snapshot:
        candidate = paths.ensure_within(course.root, course.root / material_snapshot)
        if candidate.is_dir():
            base = candidate
    if base is None:
        # Without an accepted snapshot, fall back to the working copy — and say
        # so, because the text is then not the one that was accepted.
        from . import course as course_mod
        binding = course.binding
        repository_root = binding.get("repository_root")
        if not repository_root:
            raise RetrievalError("SCOPE_UNAVAILABLE",
                                 "у курса не записан корень материалов")
        base = paths.ensure_within(course.root, course.root / repository_root)
        if not base.is_dir():
            raise RetrievalError("SCOPE_UNAVAILABLE",
                                 "каталог материалов недоступен: %s" % base)

    files = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            relative = path.relative_to(base).as_posix()
        except ValueError:
            continue
        if course.is_readable_path(relative):
            files.append((relative, path))

    return {
        "course_id": course.course_id,
        "course_revision": dict(course.revision),
        "contract_hash": course.contract_hash,
        "base": str(base),
        "using_snapshot": bool(material_snapshot),
        "include_roots": list(course.contract["student_material_roots"]),
        "exclude_roots": list(course.contract.get("excluded_material_roots") or []),
        "files": files,
    }


# --------------------------------------------------------------------------
# Text search
# --------------------------------------------------------------------------

def _iter_lines(path):
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return
    for number, line in enumerate(text.splitlines(), 1):
        yield number, line


def search_text(scope, query, *, limit=5):
    """Literal substring search over the scoped files.

    Deliberately a substring search and not a fuzzy one: a fuzzy match on a
    quotation is how a fragment that does not exist gets cited with confidence.
    Case-insensitive, because that is a property of the script, not of meaning.
    """
    if not query or not query.strip():
        raise RetrievalError("QUERY_EMPTY", "поисковый запрос пуст")
    limit = max(1, min(int(limit or 5), MAX_LIMIT))
    needle = query.strip()
    folded = needle.casefold()

    hits = []
    for relative, path in scope["files"]:
        if len(hits) >= limit:
            break
        for number, line in _iter_lines(path):
            if folded in line.casefold():
                start = max(1, number)
                hits.append({
                    "source_id": relative,
                    "relative_path": relative,
                    "locator": {"lines": {"start": start, "end": start}},
                    "text": line.strip()[:MAX_HIT_TEXT],
                    "retrieval_method": "text",
                    "score": 1.0,
                    "file_sha256": corpus.sha256_file(path) if path.is_file() else None,
                })
                break
    return hits


def search_chunks(scope, query, chunks, *, limit=5):
    """Search an installed chunk file, reporting `chunk` coordinates.

    Chunk coordinates are only ever produced when a real chunk list is present.
    Synthesising an index number for a file that has no chunks file would give a
    citation that no one can check.
    """
    if not chunks:
        raise RetrievalError("CHUNKS_UNAVAILABLE",
                             "файл фрагментов не установлен: поиск по chunk-координатам "
                             "невозможен, доступен только текстовый")
    limit = max(1, min(int(limit or 5), MAX_LIMIT))
    folded = query.strip().casefold()
    allowed = {relative for relative, _path in scope["files"]}
    hits = []
    for entry in chunks:
        if len(hits) >= limit:
            break
        text = entry.get("text") or ""
        if folded not in text.casefold():
            continue
        source = entry.get("source") or entry.get("path")
        if source and source not in allowed:
            # The chunk refers to something the contract does not allow. It is
            # dropped here, before ranking, so it can never be returned.
            continue
        hits.append({
            "source_id": source,
            "relative_path": source,
            "locator": {"chunk": {"chunk_id": int(entry.get("chunk_id", 0)),
                                  "index_hash": entry.get("index_hash") or ""}},
            "text": text[:MAX_HIT_TEXT],
            "retrieval_method": "chunk",
            "score": float(entry.get("score", 1.0)),
        })
    return hits


def search(course, query, *, limit=5, material_snapshot=None, chunks=None,
           method="auto"):
    """The entry point: scoped search with an honest retrieval method."""
    scope = resolve_scope(course, material_snapshot=material_snapshot)
    requested = method
    if method == "auto":
        method = "chunk" if chunks else "text"

    if method == "chunk":
        try:
            hits = search_chunks(scope, query, chunks, limit=limit)
        except RetrievalError:
            if requested == "chunk":
                raise
            hits = search_text(scope, query, limit=limit)
    elif method == "text":
        hits = search_text(scope, query, limit=limit)
    else:
        raise RetrievalError("METHOD_UNKNOWN", "неизвестный способ поиска: %r" % method)

    for hit in hits:
        hit["course_revision"] = dict(course.revision)
        hit["contract_hash"] = course.contract_hash
    return {
        "scope": {
            "course_id": scope["course_id"],
            "using_snapshot": scope["using_snapshot"],
            "include_roots": scope["include_roots"],
            "exclude_roots": scope["exclude_roots"],
            "files_searched": len(scope["files"]),
        },
        "hits": hits,
        "retrieval_method": method,
    }


# --------------------------------------------------------------------------
# Quote verification
# --------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")


def normalise(text):
    """Deterministic normalisation, named so a report can state what it did.

    Unicode NFKC plus whitespace collapsing. Applied only when exact matching
    fails, and always reported: a normalised match is weaker evidence than an
    exact one, and the caller is entitled to know which they got.
    """
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text or "")).strip()


def _slice_for(file_path, locator):
    """The text a locator names, or an error explaining why it cannot.

    Coordinates are resolved from the source itself. A locator that names a
    range past the end of the file is a mismatch, not something to clamp.
    """
    if "lines" in locator:
        start = int(locator["lines"]["start"])
        end = int(locator["lines"]["end"])
        if start < 1 or end < start:
            return None, "LINE_RANGE_INVALID"
        lines = file_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        if end > len(lines):
            return None, "LINE_RANGE_BEYOND_FILE"
        return "\n".join(lines[start - 1:end]), ""

    if "pdf_page" in locator:
        # A PDF page cannot be re-read without a PDF parser, which this layer
        # deliberately does not depend on. The honest answer is `unverifiable`,
        # not a plausible-looking guess from the text layer.
        return None, "PDF_PAGE_UNVERIFIABLE"

    if "chunk" in locator:
        return None, "CHUNK_UNVERIFIABLE"

    if "notebook_cell" in locator:
        return None, "NOTEBOOK_CELL_UNVERIFIABLE"

    return None, "LOCATOR_UNKNOWN"


def verify_quote(course, citation, *, material_snapshot=None):
    """Check a citation's text and coordinates against the accepted materials.

    Returns a `Citation`-shaped report. `quote_status` describes the *text*
    match; `support_status` is always `not_checked` because whether the quoted
    sentence supports the claim made from it is a reading of meaning, and
    nothing in this module reads meaning.
    """
    if course is None:
        raise RetrievalError("COURSE_NOT_ACCEPTED",
                             "курс не принят: проверять цитату не по чему")

    source_ref = citation.get("source_ref") or {}
    relative = source_ref.get("path")
    locator = source_ref.get("locator") or {}
    verbatim = citation.get("verbatim") or ""

    report = {
        "schema_version": schemas.SUPPORTED_MAJOR,
        "source_ref": source_ref,
        "verbatim": verbatim,
        "translation_ru": citation.get("translation_ru"),
        "quote_status": "unverifiable",
        "quote_status_reason": "",
        "normalisation": None,
        "support_status": "not_checked",
        "coordinate_check": "not_performed",
    }

    if not relative:
        report["quote_status_reason"] = "в ссылке не указан путь источника"
        return report

    if not course.is_readable_path(relative):
        report["quote_status"] = "unverifiable"
        report["quote_status_reason"] = (
            "путь %r вне разрешённых материалов курса: цитата не проверяется "
            "по закрытым материалам" % relative
        )
        return report

    scope = resolve_scope(course, material_snapshot=material_snapshot)

    # Use the scoped file list rather than composing a path here: it has already
    # applied the contract's exclusions, and a second, independent resolution
    # would be the weaker of the two boundaries.
    resolved = None
    for candidate_relative, candidate_path in scope["files"]:
        if candidate_relative == relative:
            resolved = candidate_path
            break
    if resolved is None:
        report["quote_status_reason"] = (
            "файл %r не найден среди разрешённых материалов" % relative
        )
        return report

    declared = source_ref.get("sha256")
    actual = corpus.sha256_file(resolved)
    if declared and declared != actual:
        report["quote_status"] = "mismatch"
        report["quote_status_reason"] = (
            "содержимое файла изменилось: ожидался sha256 %s, сейчас %s. "
            "Координаты относятся к другой версии текста."
            % (declared[:16], actual[:16])
        )
        report["coordinate_check"] = "file_changed"
        return report

    excerpt, problem = _slice_for(resolved, locator)
    if excerpt is None:
        report["quote_status"] = "unverifiable"
        report["quote_status_reason"] = {
            "LINE_RANGE_INVALID": "некорректный диапазон строк",
            "LINE_RANGE_BEYOND_FILE": "диапазон строк выходит за конец файла",
            "PDF_PAGE_UNVERIFIABLE": "проверка страницы PDF не выполняется этим слоем",
            "CHUNK_UNVERIFIABLE": "проверка по chunk выполняется вместе с индексом",
            "NOTEBOOK_CELL_UNVERIFIABLE": "проверка ячейки ноутбука не выполняется этим слоем",
            "LOCATOR_UNKNOWN": "неизвестный вид координат",
        }.get(problem, problem)
        report["coordinate_check"] = problem.lower()
        return report

    report["coordinate_check"] = "ok"

    if not verbatim.strip():
        report["quote_status"] = "unverifiable"
        report["quote_status_reason"] = "цитата пуста"
        return report

    if verbatim in excerpt:
        report["quote_status"] = "exact"
        report["quote_status_reason"] = "точное вхождение в указанном фрагменте"
        return report

    if normalise(verbatim) in normalise(excerpt):
        report["quote_status"] = "normalized_match"
        report["normalisation"] = "NFKC + схлопывание пробелов"
        report["quote_status_reason"] = (
            "совпадение после нормализации Unicode и пробелов; это слабее точного"
        )
        return report

    # The text may exist elsewhere in the same file. Saying so is useful (the
    # locator is wrong, not the quotation) and is still a mismatch.
    whole = resolved.read_text(encoding="utf-8-sig", errors="replace")
    if verbatim in whole:
        report["quote_status"] = "mismatch"
        report["quote_status_reason"] = (
            "текст найден в этом файле, но НЕ в указанном фрагменте: "
            "ошибка координат, а не цитаты"
        )
        return report

    report["quote_status"] = "mismatch"
    report["quote_status_reason"] = "цитата не найдена в источнике"
    return report


def validate_citation(report):
    """Whether a verified citation may be shown to the learner as evidence."""
    if report["quote_status"] == "exact":
        return True, []
    if report["quote_status"] == "normalized_match":
        return True, ["совпадение после нормализации: показывайте с оговоркой"]
    return False, [report.get("quote_status_reason") or "цитата не подтверждена"]
