#!/usr/bin/env python3
"""Validates every JSON doc under data/ against its schema, plus the semantic
rules the schemas can't express (vocab membership, version-registry lookups,
filename/context/dialect consistency, duplicate files).

See README.md for the checklist this enforces. Exits non-zero (printing one
line per violation) if anything fails.

Usage:
    python scripts/validate.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "dialects"
SCHEMA_DIR = REPO_ROOT / "schema"

VERSION_PATTERN = re.compile(r"^\d+(\.\d+){0,2}$")
URL_PATTERN = re.compile(r"^https?://")

DOC_TYPES = {
    "messages": "message.schema.json",
    "enums": "enum.schema.json",
    "mission": "command.schema.json",
    "command": "command.schema.json",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def build_validators() -> dict[str, Draft202012Validator]:
    resources = []
    schemas = {}
    for schema_path in SCHEMA_DIR.glob("*.schema.json"):
        schema = load_json(schema_path)
        schemas[schema_path.name] = schema
        resources.append((schema["$id"], Resource.from_contents(schema)))
    registry = Registry().with_resources(resources)
    return {
        name: Draft202012Validator(schema, registry=registry)
        for name, schema in schemas.items()
    }


def iter_data_files():
    for path in sorted(DATA_DIR.rglob("*.json")):
        rel_parts = path.relative_to(DATA_DIR).parts
        # <dialect>/(messages|enums)/<name>.json  or  <dialect>/mav_cmd/<mission|command>/<name>.json
        if len(rel_parts) == 3:
            dialect, kind, _ = rel_parts
            context = None
        elif len(rel_parts) == 4 and rel_parts[1] == "mav_cmd":
            dialect, _, context, _ = rel_parts
            kind = context
        else:
            yield path, None, None, None
            continue
        yield path, dialect, kind, context


def check_version_field(val, field_name: str, stack: str, versions: dict, errors: list[str], where: str) -> None:
    """Validates a version-string-or-'main' field (added_version, deprecated_
    version, removed_version, last_checked_version): must be 'main' or a
    known released version for that stack. Skips `true` (added_version's
    "implemented, version unknown" case) and None (field absent)."""
    if val is None or val is True or val == "main":
        return
    if not VERSION_PATTERN.match(str(val)):
        errors.append(f"{where}: {field_name}={val!r} is not 'main' or a version string")
    elif val not in versions.get(stack, []):
        errors.append(f"{where}: {field_name}={val!r} is not a known {stack} release (see schema/versions.json)")


def check_supported_object(supported: dict, stack: str, versions: dict, errors: list[str], where: str) -> None:
    for field_name in ("added_version", "deprecated_version", "removed_version"):
        check_version_field(supported.get(field_name), field_name, stack, versions, errors, where)


def _last_statement(statement_or_history):
    if isinstance(statement_or_history, list):
        return statement_or_history[-1] if statement_or_history else None
    return statement_or_history


def _is_implemented(statement_or_history) -> bool:
    """True iff the (possibly historical) statement's current entry is a
    confirmed-implemented object, as opposed to false/null/not-applicable."""
    statement = _last_statement(statement_or_history)
    return isinstance(statement, dict) and isinstance(statement.get("supported"), dict)


def _effective_statement(by_variant: dict, variant: str):
    """Resolves a variant against its stack's "default", per the propagation
    rule: a variant with no key of its own inherits "default"."""
    if variant in by_variant:
        return by_variant[variant]
    return by_variant.get("default")


def check_sub_entity_gating(entity_compat: dict, sub_items: list[dict], sub_key: str, errors: list[str], where: str) -> None:
    """Enforces CLAUDE.md's sub-entity gating rule: a field/param/value may
    carry a compatibility entry for a given (stack, variant) iff the parent's
    effective status there is a confirmed-implemented object. Checks both
    directions — required-but-missing, and present-but-not-warranted.

    Note: this resolves each (stack, variant) independently by looking only
    at keys actually present on each side (falling back to that side's own
    "default"). A sub-entity that inherits its own unqualified "default" can
    therefore appear to satisfy a parent variant override that diverges from
    the parent's default in the false/not-implemented direction; that
    combination doesn't occur anywhere in the data today and is a known,
    accepted rough edge rather than something worth extra machinery for.
    """
    for i, item in enumerate(sub_items):
        item_label = f"{where}: {sub_key}[{i}] ({item.get('name')!r})"
        child_compat = item.get("compatibility") or {}
        if sub_key == "params" and item.get("name") == "Empty":
            # A reserved/undocumented upstream param slot (mavlink_xml.py's
            # own sentinel name for "no label in the XML") isn't a feature
            # any stack could support or not — exempt from gating entirely,
            # not merely "not yet required". Never carries compatibility.
            if child_compat:
                errors.append(f"{item_label}: reserved param ('Empty') must not carry compatibility")
            continue
        for stack in set(entity_compat) | set(child_compat):
            parent_by_variant = entity_compat.get(stack, {})
            child_by_variant = child_compat.get(stack, {})
            for variant in set(parent_by_variant) | set(child_by_variant):
                parent_implemented = _is_implemented(_effective_statement(parent_by_variant, variant))
                child_present = _effective_statement(child_by_variant, variant) is not None
                if parent_implemented and not child_present:
                    errors.append(f"{item_label}: missing compatibility for {stack}.{variant} (parent is implemented there)")
                elif not parent_implemented and child_present:
                    errors.append(f"{item_label}: has compatibility for {stack}.{variant} but parent is not implemented there (should be omitted, not 'unknown')")


def check_compatibility(compat: dict, vocab: dict, versions: dict, errors: list[str], where: str, allow_not_applicable: bool) -> None:
    for stack, by_variant in compat.items():
        if stack not in vocab["stacks"]:
            errors.append(f"{where}: unknown stack {stack!r} (not in schema/vocab.json)")
            continue
        valid_variants = {"default", *vocab["variants"].get(stack, [])}
        for variant, statement_or_history in by_variant.items():
            if variant not in valid_variants:
                errors.append(f"{where}: unknown variant {variant!r} for stack {stack!r}")
            statements = (
                statement_or_history if isinstance(statement_or_history, list) else [statement_or_history]
            )
            for statement in statements:
                basis = statement.get("basis")
                if basis not in vocab["basis"]:
                    errors.append(f"{where}: {stack}.{variant}: unknown basis {basis!r}")
                impl_url = statement.get("impl_url")
                if impl_url is not None and not URL_PATTERN.match(impl_url):
                    errors.append(f"{where}: {stack}.{variant}: impl_url {impl_url!r} is not http(s)")
                check_version_field(
                    statement.get("last_checked_version"), "last_checked_version",
                    stack, versions, errors, f"{where}: {stack}.{variant}",
                )
                supported = statement.get("supported")
                if supported == "not-applicable" and not allow_not_applicable:
                    errors.append(f"{where}: {stack}.{variant}: 'not-applicable' only valid in command docs")
                if isinstance(supported, dict):
                    check_supported_object(supported, stack, versions, errors, f"{where}: {stack}.{variant}")


def main() -> int:
    vocab = load_json(SCHEMA_DIR / "vocab.json")
    versions = load_json(SCHEMA_DIR / "versions.json")
    validators = build_validators()

    errors: list[str] = []
    seen_names: dict[tuple, Path] = {}

    for path, dialect, kind, context in iter_data_files():
        rel = path.relative_to(REPO_ROOT)
        if dialect is None:
            errors.append(f"{rel}: unexpected file location")
            continue
        schema_file = DOC_TYPES.get(kind)
        if schema_file is None:
            errors.append(f"{rel}: unrecognized doc kind {kind!r}")
            continue

        doc = load_json(path)
        for err in validators[schema_file].iter_errors(doc):
            errors.append(f"{rel}: schema violation at {'/'.join(str(p) for p in err.path)}: {err.message}")

        if doc.get("name") != path.stem:
            errors.append(f"{rel}: filename does not match name field {doc.get('name')!r}")
        if doc.get("dialect") != dialect:
            errors.append(f"{rel}: dialect field {doc.get('dialect')!r} does not match directory {dialect!r}")
        if context is not None and doc.get("context") != context:
            errors.append(f"{rel}: context field {doc.get('context')!r} does not match directory {context!r}")

        dup_key = (dialect, kind, context, doc.get("name"))
        if dup_key in seen_names:
            errors.append(f"{rel}: duplicate entity name (also in {seen_names[dup_key].relative_to(REPO_ROOT)})")
        else:
            seen_names[dup_key] = path

        compat = doc.get("compatibility")
        if isinstance(compat, dict):
            check_compatibility(compat, vocab, versions, errors, f"{rel}: compatibility", kind in ("mission", "command"))
        for sub_key in ("fields", "values", "params"):
            sub_items = doc.get(sub_key, [])
            for i, item in enumerate(sub_items):
                sub_compat = item.get("compatibility")
                if isinstance(sub_compat, dict):
                    check_compatibility(
                        sub_compat, vocab, versions, errors,
                        f"{rel}: {sub_key}[{i}] ({item.get('name')})", kind in ("mission", "command"),
                    )
            if isinstance(compat, dict) and sub_items:
                check_sub_entity_gating(compat, sub_items, sub_key, errors, str(rel))

    if errors:
        for e in errors:
            print(e)
        print(f"\n{len(errors)} validation error(s).")
        return 1

    print(f"OK: {len(seen_names)} file(s) validated, no errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
