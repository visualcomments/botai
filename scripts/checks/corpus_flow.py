"""End-to-end check of the corpus and retrieval path, without a network.

Builds a real corpus archive, serves it from a local file, installs it through
the public API, then verifies the properties that matter: the corpus is scoped
to its course, an excluded root is unreachable, a quote with wrong coordinates
is refused, and a failed re-install leaves the working corpus alone.

Run:
    python scripts/checks/corpus_flow.py
"""
from __future__ import annotations

import hashlib
import io
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from botai_core import corpus, course, retrieval  # noqa: E402

failures = 0


def check(name, condition, detail=""):
    global failures
    if condition:
        print("  [ok]   %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        failures += 1


def build_archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def main():
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "workspace"
    url_root = tmp / "served"
    url_root.mkdir(parents=True)

    # 1. A course, accepted.
    course_dir = root / "courses" / "minimal-diff"
    course_dir.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "examples" / "minimal-course", course_dir)
    course.accept(root, course_dir, slug="minimal-diff",
                  repository_root="courses/minimal-diff")
    accepted = course.load_accepted(root, "minimal-diff")
    print("### курс принят: %s" % accepted.course_id)

    # 2. A real corpus archive with real hashes.
    texts = {
        "txt/bayes.txt": "Теорема Байеса связывает апостериорную вероятность "
                         "с правдоподобием и априорной вероятностью.",
        "txt/diff.txt": "git diff сравнивает рабочую копию с индексом.",
    }
    payload = build_archive(texts)
    manifest = {
        "schema_version": 2,
        "corpus_id": "minimal-corpus",
        "version": "1",
        "source_revision": "2026-01",
        "artifacts": [{
            "artifact_id": "texts",
            "url": "https://example.invalid/texts.zip",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "media_type": "application/zip",
            "unpack": "zip",
            "compressed_bytes": len(payload),
            "unpacked_bytes_max": 1024 * 1024,
            "auth_required": False,
        }],
        "files": [
            {"path": name, "sha256": hashlib.sha256(content.encode()).hexdigest(),
             "byte_size": len(content.encode()), "role": "text",
             "artifact_id": "texts"}
            for name, content in texts.items()
        ],
        "index": {"kind": "none"},
    }
    manifest_path = url_root / "corpus-manifest.json"
    manifest_path.write_text(
        __import__("json").dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print()
    print("### 3. корпус отсутствует — статус не выдаёт желаемое за факт")
    fresh = corpus.status(root, "minimal-diff", manifest)
    check("до установки статус не ready", fresh["status"] != "ready", fresh["status"])

    print()
    print("### 4. установка из манифеста (байты подаются напрямую, сети нет)")
    report = corpus.acquire(root, "minimal-diff", manifest,
                            fetch=lambda url: payload)
    check("корпус установлен и подтверждён", report["status"] == "ready",
          str(report.get("error") or report["status"]))
    check("проверено столько файлов, сколько объявлено",
          report["checks"]["checked"] == len(texts),
          str(report["checks"]))

    print()
    print("### 5. повторная установка не переделывает работу")
    again = corpus.acquire(root, "minimal-diff", manifest, fetch=lambda url: payload)
    check("повтор вернул ready", again["status"] == "ready")
    check("повтор не скачивал заново",
          any("не нужен" in w for w in again["warnings"]), str(again["warnings"]))

    print()
    print("### 6. корпус другого курса недоступен")
    other = corpus.status(root, "other-course", manifest)
    check("другой курс не считается готовым", other["status"] != "ready",
          other["status"])

    print()
    print("### 7. поиск ограничен принятыми материалами")
    result = retrieval.search(accepted, "Байеса", limit=5,
                              material_snapshot=accepted.binding["material_snapshot"])
    print("       область: файлов %d, исключено %s"
          % (result["scope"]["files_searched"], result["scope"]["exclude_roots"]))
    check("поиск выполнен по разрешённым файлам", "scope" in result)

    # Plant bait under the excluded root and confirm it is invisible.
    bait = root / accepted.binding["material_snapshot"] / "teacher"
    bait.mkdir(parents=True, exist_ok=True)
    (bait / "answers.md").write_text("ЭТАЛОННЫЙ ОТВЕТ 42", encoding="utf-8")
    baited = retrieval.search(accepted, "ЭТАЛОННЫЙ ОТВЕТ 42", limit=5,
                              material_snapshot=accepted.binding["material_snapshot"])
    check("исключённый корень не виден поиску", len(baited["hits"]) == 0,
          str(baited["hits"]))

    print()
    print("### 8. проверка цитаты: точная и с неверными координатами")
    lesson = accepted.binding["material_snapshot"] + "/lessons/01-working-tree.md"
    lines = (root / lesson).read_text(encoding="utf-8").splitlines()
    quote = lines[10].strip()

    good = retrieval.verify_quote(accepted, {
        "source_ref": {"path": "lessons/01-working-tree.md",
                       "locator": {"lines": {"start": 11, "end": 11}}},
        "verbatim": quote,
    }, material_snapshot=accepted.binding["material_snapshot"])
    check("точная цитата подтверждена", good["quote_status"] == "exact",
          good["quote_status"])
    check("смысл не объявляется проверенным",
          good["support_status"] == "not_checked", good["support_status"])

    bad = retrieval.verify_quote(accepted, {
        "source_ref": {"path": "lessons/01-working-tree.md",
                       "locator": {"lines": {"start": 1, "end": 1}}},
        "verbatim": quote,
    }, material_snapshot=accepted.binding["material_snapshot"])
    check("верный текст в неверном месте отклонён",
          bad["quote_status"] == "mismatch", bad["quote_status"])
    allowed, _ = retrieval.validate_citation(bad)
    check("такую цитату нельзя показать как точную", allowed is False)

    print()
    print("### 9. сбой обновления не разрушает рабочий корпус")
    broken = dict(manifest)
    broken["artifacts"] = [dict(manifest["artifacts"][0])]
    broken["artifacts"][0]["sha256"] = "f" * 64
    failed = corpus.acquire(root, "minimal-diff", broken,
                            fetch=lambda url: payload)
    check("сбойная установка отклонена", failed["status"] == "failed",
          failed["status"])
    still = corpus.status(root, "minimal-diff", manifest)
    check("прежний корпус цел", still["status"] == "ready", still["status"])

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if failures == 0:
        print("CORPUS FLOW OK")
    else:
        print("CORPUS FLOW FAILURES: %d" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
