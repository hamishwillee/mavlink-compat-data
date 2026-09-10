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
        # <dialect>/(messages|enums)/<name>.json  or  <dialect>/commands/<mission|command>/<name>.json
        if len(rel_parts) == 3:
            dialect, kind, _ = rel_parts
            context = None
        elif len(rel_parts) == 4 and rel_parts[1] == "commands":
            dialect, _, context, _ = rel_parts
            kind = context
        else:
            yield path, None, None, None
            continue
        yield path, dialect, kind, context


def check_supported_object(supported: dict, stack: str, versions: dict, errors: list[str], where: str) -> None:
    added = supported.get("added_version")
    for field_name in ("deprecated_version", "removed_version"):
        val = supported.get(field_name)
        if val is None:
            continue
        if val != "main" and not VERSION_PATTERN.match(str(val)):
            errors.append(f"{where}: {field_name}={val!r} is not 'main' or a version string")
        elif val != "main" and val not in versions.get(stack, []):
            errors.append(f"{where}: {field_name}={val!r} is not a known {stack} release (see schema/versions.json)")
    if isinstance(added, str) and added != "main":
        if not VERSION_PATTERN.match(added):
            errors.append(f"{where}: added_version={added!r} is not 'main', true, or a version string")
        elif added not in versions.get(stack, []):
            errors.append(f"{where}: added_version={added!r} is not a known {stack} release (see schema/versions.json)")


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
            for i, item in enumerate(doc.get(sub_key, [])):
                sub_compat = item.get("compatibility")
                if isinstance(sub_compat, dict):
                    check_compatibility(
                        sub_compat, vocab, versions, errors,
                        f"{rel}: {sub_key}[{i}] ({item.get('name')})", kind in ("mission", "command"),
                    )

    if errors:
        for e in errors:
            print(e)
        print(f"\n{len(errors)} validation error(s).")
        return 1

    print(f"OK: {len(seen_names)} file(s) validated, no errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
