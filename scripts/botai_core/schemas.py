# -*- coding: utf-8 -*-
"""Contract validation for the v2 core.

Every structure that crosses a tool boundary is validated against a local JSON
Schema before it is stored or acted on. Two properties matter:

* **Resolution is local.** A `$ref` is looked up in the schemas directory and
  never fetched. A schema pointing at a URL is rejected rather than retrieved:
  validating an untrusted document must not become an outbound request, and a
  remote schema can change under a pinned contract.
* **Unknown major versions are errors.** A document claiming `schema_version: 3`
  is not read best-effort — a future format read as if it were this one is how
  a field silently changes meaning.

`jsonschema` is the one third-party dependency of the core; it is pinned in
`requirements-core.lock`. When it is missing, callers get a clear
`SCHEMAS_UNAVAILABLE` result with the bootstrap command rather than a traceback
or a silent skip — an unvalidated contract must never look like a validated one.
"""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "schemas" / "v2"
SUPPORTED_MAJOR = 2

# Check kinds, mirrored from common.schema.json so callers can validate a kind
# without loading a schema. Kept in sync by `test_schemas.py`, which compares
# this tuple against the schema's enum: two lists that can drift silently are
# worse than one.
SUPPORTED_CHECK_KINDS = ("explain", "predict", "apply", "transfer", "critique", "diagnostic")

# Contract name -> schema file. Populated explicitly so a typo is a KeyError at
# the call site, not a silently skipped validation.
CONTRACTS = {
    "course": "course.schema.json",
    "track": "track.schema.json",
    "progress": "progress.schema.json",
    "binding": "binding.schema.json",
    "policy": "policy.schema.json",
    "session": "session.schema.json",
    # Entities are stored individually, so they are validated individually:
    # a rule that only applied to the containing document would not run at the
    # moment of the write.
    "objective_state": "progress.schema.json#/$defs/objective_state",
    "result": "common.schema.json",
}


class SchemaError(ValueError):
    """A document that does not satisfy its contract."""

    def __init__(self, code, message, errors=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.errors = errors or []


class SchemasUnavailable(RuntimeError):
    """`jsonschema` is not importable in this interpreter."""

    BOOTSTRAP = (
        "python3 -m venv .botai/runtime/venv && "
        ".botai/runtime/venv/bin/python -m pip install --require-hashes "
        "-r requirements-core.lock"
    )


def _import_validator():
    try:
        import jsonschema  # noqa: F401
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise SchemasUnavailable(
            "не найдена библиотека jsonschema; контракты не валидируются. "
            "Установите зависимости ядра: %s (%s)" % (SchemasUnavailable.BOOTSTRAP, e)
        )
    return jsonschema


def _load_schema_file(path):
    """Load one schema, refusing any $ref that is not a local file name."""
    if not path.is_file():
        raise SchemaError("SCHEMA_MISSING", "схема не найдена: %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SchemaError("SCHEMA_UNREADABLE", "схема повреждена: %s (%s)" % (path, e))

    for ref in _iter_refs(data):
        target = ref.split("#", 1)[0]
        if not target:
            continue  # a pure fragment: same document
        if "://" in target or target.startswith("//"):
            raise SchemaError(
                "SCHEMA_REMOTE_REF",
                "схема %s ссылается на внешний документ (%s); валидация обязана "
                "разрешаться локально" % (path.name, ref),
            )
        base = Path(target)
        if base.name != target or base.suffix != ".json":
            raise SchemaError(
                "SCHEMA_REF_ESCAPE",
                "схема %s ссылается на файл вне каталога схем: %s" % (path.name, ref),
            )
        if not (path.parent / base).is_file():
            raise SchemaError(
                "SCHEMA_REF_MISSING",
                "схема %s ссылается на отсутствующий файл: %s" % (path.name, ref),
            )
    return data


def _iter_refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)


def build_registry(schema_dir=None):
    """A referencing registry over the local schema directory."""
    jsonschema = _import_validator()
    from referencing import Registry, Resource

    schema_dir = Path(schema_dir or SCHEMA_DIR)
    resources = []
    for name in sorted(p.name for p in schema_dir.glob("*.json")):
        data = _load_schema_file(schema_dir / name)
        uri = data.get("$id") or name
        resources.append((uri, Resource.from_contents(data)))
        # Also register by bare filename: the schemas reference each other that
        # way, which keeps them readable without inventing absolute ids.
        if uri != name:
            resources.append((name, Resource.from_contents(data)))
    return jsonschema, Registry().with_resources(resources)


def schema_for(contract, schema_dir=None):
    """Load the schema for a named contract, checking local references.

    A contract may name a fragment (`file.schema.json#/$defs/name`), which lets
    an entity be validated by the same rules that apply inside its document.
    """
    schema_dir = Path(schema_dir or SCHEMA_DIR)
    name = CONTRACTS.get(contract)
    if not name:
        raise SchemaError("CONTRACT_UNKNOWN", "неизвестный контракт: %r" % contract)

    file_part, _, fragment = name.partition("#")
    document = _load_schema_file(schema_dir / file_part)
    if not fragment:
        return document

    node = document
    for step in fragment.strip("/").split("/"):
        if not step:
            continue
        if not isinstance(node, dict) or step not in node:
            raise SchemaError(
                "SCHEMA_FRAGMENT_MISSING",
                "контракт %r ссылается на отсутствующий фрагмент %r" % (contract, fragment),
            )
        node = node[step]
    # A `$id` must not carry a fragment (2020-12 metaschema forbids it), so the
    # resolved fragment simply inherits the document's declarations and is
    # identified by its `$ref` site instead.
    resolved = dict(node)
    resolved.setdefault("$schema", document.get("$schema"))
    resolved["$comment"] = "resolved from %s#%s" % (file_part, fragment)
    return resolved


def validate(document, contract, *, schema_dir=None):
    """Validate `document` against `contract`. Raises SchemaError on failure.

    Returns the document so the call reads as a guard clause at the call site.
    """
    if not isinstance(document, dict):
        raise SchemaError("NOT_AN_OBJECT", "ожидался JSON-объект, получено %s"
                          % type(document).__name__)

    declared = document.get("schema_version")
    if declared is not None and declared != SUPPORTED_MAJOR:
        raise SchemaError(
            "SCHEMA_VERSION_UNSUPPORTED",
            "версия контракта %r не поддерживается (поддерживается %d): "
            "документ не читается «как получится», потому что поля могли "
            "изменить смысл" % (declared, SUPPORTED_MAJOR),
        )

    jsonschema, registry = build_registry(schema_dir)
    schema = schema_for(contract, schema_dir)

    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema, registry=registry)

    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    if errors:
        rendered = []
        for err in errors[:12]:
            location = "/".join(str(p) for p in err.absolute_path) or "(корень)"
            rendered.append("%s: %s" % (location, err.message))
        raise SchemaError(
            "CONTRACT_INVALID",
            "документ не соответствует контракту %r: %s" % (contract, "; ".join(rendered)),
            errors=errors,
        )
    return document


def validation_status(contract, schema_dir=None):
    """`ok` / `unavailable` / `broken` — for `doctor` to report honestly."""
    try:
        schema = schema_for(contract, schema_dir)
        jsonschema, registry = build_registry(schema_dir)
        jsonschema.validators.validator_for(schema).check_schema(schema)
    except SchemasUnavailable as e:
        return "unavailable", str(e)
    except SchemaError as e:
        return "broken", e.message
    return "ok", "schema %s loaded" % schema.get("$id", contract)
