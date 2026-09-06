"""JSON Schema validation of a content pack (``05.1``).

The schemas in ``tor/content/schema/`` are the pack format's *shape* contract: required
fields, types, enumerated values. They run first, so a structural mistake is reported as a
structural mistake. What a schema cannot express — that an id resolves, that a table covers
its domain, that a Cultural Virtue names a real culture — is ``05.1.1``'s referential and
semantic pass in :mod:`tor.content.loader`, which runs after.

Every failure is a :class:`~tor.errors.ContentError` carrying the file and a JSON pointer,
so a pack author is told exactly which node is wrong.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from tor.errors import ContentError

__all__ = ["SCHEMA_DIR", "schema_for", "validate_document"]

#: Where the shipped schemas live. ``00-README.md`` §6 names this directory.
SCHEMA_DIR = Path(__file__).parent / "schema"

#: Pack file name (or, for a directory of them, the directory) → schema file name.
_SCHEMA_FOR_FILE: dict[str, str] = {
    "pack.json": "pack.schema.json",
    "cultures.json": "cultures.schema.json",
    "callings.json": "callings.schema.json",
    "skills.json": "skills.schema.json",
    "weapons.json": "weapons.schema.json",
    "armour.json": "armour.schema.json",
    "shields.json": "shields.schema.json",
    "shadow_paths.json": "shadow_paths.schema.json",
    "adversaries.json": "adversaries.schema.json",
    "patrons.json": "patrons.schema.json",
    "standards_of_living.json": "standards_of_living.schema.json",
    "undertakings.json": "undertakings.schema.json",
    "songs.json": "songs.schema.json",
    "tables/": "table.schema.json",
    "names/": "names.schema.json",
}


def schema_for(name: str) -> str | None:
    """The schema file governing a pack file, or ``None`` for the effect files.

    The nine effect files share one shape, so they all resolve to ``effects.schema.json``.
    """
    if name in _SCHEMA_FOR_FILE:
        return _SCHEMA_FOR_FILE[name]
    return None


@cache
def _registry() -> Registry[Any]:
    """Every shipped schema, keyed by bare filename so ``$ref`` resolves offline."""
    resources = []
    for file in sorted(SCHEMA_DIR.glob("*.schema.json")):
        contents = json.loads(file.read_text())
        resource = Resource.from_contents(contents, default_specification=DRAFT202012)
        resources.append((file.name, resource))
    return Registry().with_resources(resources)


@cache
def _validator(schema_name: str) -> Draft202012Validator:
    file = SCHEMA_DIR / schema_name
    if not file.exists():  # pragma: no cover - a packaging error, not a pack error
        raise ContentError(f"the engine is missing schema {schema_name!r}", file=file)
    schema = json.loads(file.read_text())
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:  # pragma: no cover - a packaging error, not a pack error
        raise ContentError(f"schema {schema_name!r} is itself invalid: {exc.message}") from exc
    return Draft202012Validator(schema, registry=_registry())


def _pointer(error: ValidationError) -> str:
    return "/" + "/".join(str(part) for part in error.absolute_path)


def validate_document(data: object, *, schema_name: str, file: Path) -> None:
    """Check one decoded pack file against its schema, reporting the first failure."""
    errors = sorted(_validator(schema_name).iter_errors(data), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    first = errors[0]
    raise ContentError(
        f"{file.name} does not match {schema_name}: {first.message}",
        file=file,
        pointer=_pointer(first),
    )
