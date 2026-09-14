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
    "definitions": "mav_cmd_definition.schema.json",
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
        # <dialect>/(messages|enums)/<name>.json  or
        # <dialect>/mav_cmd/(mission|command|definitions)/<name>.json
        if len(rel_parts) == 3:
            dialect, kind, _ = rel_parts
            context = None
        elif len(rel_parts) == 4 and rel_parts[1] == "mav_cmd":
            dialect, _, sub, _ = rel_parts
            if sub == "definitions":
                kind, context = "definitions", None
            else:
                kind, context = sub, sub
        else:
            yield path, None, None, None
            continue
        yield path, dialect, kind, context


def check_version_field(
    val, field_name: str, stack: str, versions: dict, errors: list[str], where: str, *, allow_main: bool = True
) -> None:
    """Validates a version-string(-or-'main') field. `allow_main=True` (the
    default) covers added_version/deprecated_version/removed_version, where
    'main' legitimately means "happened on the dev branch, not yet
    released". `allow_main=False` covers last_checked_version, which must be
    a specific released version: its job is to be a fixed baseline
    comparable against future releases ("has a newer release shipped since
    this was checked?"), and 'main' can't serve that purpose since it's a
    moving target -- see schema/compatibility-entry.schema.json's
    releasedVersion def. Either way, skips `true` (added_version's
    "implemented, version unknown" case) and None (field absent)."""
    if val is None or val is True:
        return
    if val == "main":
        if not allow_main:
            errors.append(f"{where}: {field_name}='main' is not allowed -- must be a specific released version")
        return
    if not VERSION_PATTERN.match(str(val)):
        expected = "a version string" if not allow_main else "'main' or a version string"
        errors.append(f"{where}: {field_name}={val!r} is not {expected}")
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
    """Enforces CLAUDE.md's sub-entity gating rule for fields/values (messages/
    enums): a field/value may carry a compatibility entry for a given (stack,
    variant) iff the parent's effective status there is a confirmed-implemented
    object. Checks both directions — required-but-missing, and
    present-but-not-warranted. Commands/params are handled separately by
    check_command_compatibility/check_frame_status instead.

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
        valid_variants = {"default", *vocab["frames"].get(stack, [])}
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
                    allow_main=False,
                )
                supported = statement.get("supported")
                if supported == "not-applicable" and not allow_not_applicable:
                    errors.append(f"{where}: {stack}.{variant}: 'not-applicable' only valid in command docs")
                if isinstance(supported, dict):
                    check_supported_object(supported, stack, versions, errors, f"{where}: {stack}.{variant}")


def _check_statement_or_history(statement_or_history, stack: str, versions: dict, errors: list[str], where: str, *, allow_not_applicable: bool, basis_required: bool = True) -> None:
    """basis_required=False covers a command frame's params/mav_frames.rejects_unsupported
    entries, where a missing 'basis' means 'same basis as the enclosing frame'
    -- a documentation convention this function doesn't resolve, just permits
    (see CLAUDE.md and schema/compatibility-entry.schema.json's
    frameSubStatement/paramStatement)."""
    statements = statement_or_history if isinstance(statement_or_history, list) else [statement_or_history]
    for statement in statements:
        basis = statement.get("basis")
        if basis is None:
            if basis_required:
                errors.append(f"{where}: unknown basis {basis!r}")
        elif basis not in ("unknown", "code-inspection", "testing", "verified"):
            errors.append(f"{where}: unknown basis {basis!r}")
        impl_url = statement.get("impl_url")
        if impl_url is not None and not URL_PATTERN.match(impl_url):
            errors.append(f"{where}: impl_url {impl_url!r} is not http(s)")
        check_version_field(statement.get("last_checked_version"), "last_checked_version", stack, versions, errors, where, allow_main=False)
        supported = statement.get("supported")
        if supported == "not-applicable" and not allow_not_applicable:
            errors.append(f"{where}: 'not-applicable' only valid in command docs")
        if isinstance(supported, dict):
            check_supported_object(supported, stack, versions, errors, where)


def check_param_statement(key: str, statement_or_history, stack: str, versions: dict, errors: list[str], where: str) -> None:
    """A param's own frameStatus.params.<key> entry. Reserved '<index>_Empty'
    keys are allowed here (unlike an ordinary param) purely to carry
    accept_nan_or_int32max/nacks_on_non_sentinel_value, with 'supported' fixed
    to 'not-applicable' -- see schema/compatibility-entry.schema.json's
    paramStatement and CLAUDE.md."""
    _check_statement_or_history(statement_or_history, stack, versions, errors, where, allow_not_applicable=True, basis_required=False)
    last = _last_statement(statement_or_history)
    if not isinstance(last, dict):
        return
    supported = last.get("supported")
    if key.endswith("_Empty") and supported != "not-applicable":
        errors.append(f"{where}: reserved param ('Empty') must have supported == 'not-applicable'")
    if isinstance(supported, dict):
        if "nacks_on_non_sentinel_value" in last:
            errors.append(f"{where}: nacks_on_non_sentinel_value only valid when supported is not a confirmed-implemented object")
        if "accept_nan_or_int32max" in last:
            errors.append(f"{where}: accept_nan_or_int32max belongs inside 'supported' when supported is a confirmed-implemented object, not as a top-level sibling")


def check_frame_status(frame: dict, stack: str, versions: dict, errors: list[str], where: str, valid_param_keys: set[str], all_param_keys: set[str], mav_frame_names: set[str]) -> None:
    if frame.get("basis") not in ("unknown", "code-inspection", "testing", "verified"):
        errors.append(f"{where}: unknown basis {frame.get('basis')!r}")
    impl_url = frame.get("impl_url")
    if impl_url is not None and not URL_PATTERN.match(impl_url):
        errors.append(f"{where}: impl_url {impl_url!r} is not http(s)")
    check_version_field(frame.get("last_checked_version"), "last_checked_version", stack, versions, errors, where, allow_main=False)
    check_version_field(frame.get("earliest_checked_version"), "earliest_checked_version", stack, versions, errors, where, allow_main=False)

    supported = frame.get("supported")
    if isinstance(supported, dict):
        check_supported_object(supported, stack, versions, errors, where)
    added_true = isinstance(supported, dict) and supported.get("added_version") is True
    if "earliest_checked_version" in frame and not added_true:
        errors.append(f"{where}: earliest_checked_version only valid when supported.added_version is `true`")

    frame_implemented = isinstance(supported, dict)

    params = frame.get("params")
    if params is not None:
        for key, pstat in params.items():
            if key not in all_param_keys:
                errors.append(f"{where}: params key {key!r} does not match a current param")
            check_param_statement(key, pstat, stack, versions, errors, f"{where}: params.{key}")
        if frame_implemented:
            for key in valid_param_keys - set(params):
                errors.append(f"{where}: missing params[{key!r}] (frame is confirmed implemented)")
    if not frame_implemented and params:
        errors.append(f"{where}: has params entries but frame is not confirmed implemented")

    mav_frames = frame.get("mav_frames")
    if mav_frames is not None:
        if not frame_implemented:
            errors.append(f"{where}: has mav_frames but frame is not confirmed implemented")
        for name in mav_frames.get("supported", []):
            if name not in mav_frame_names:
                errors.append(f"{where}: mav_frames.supported {name!r} is not a known MAV_FRAME value")
        default_frame = mav_frames.get("default_frame_command_long")
        if default_frame is not None and default_frame not in mav_frame_names:
            errors.append(f"{where}: mav_frames.default_frame_command_long {default_frame!r} is not a known MAV_FRAME value")
        rejects = mav_frames.get("rejects_unsupported")
        if rejects is not None:
            _check_statement_or_history(rejects, stack, versions, errors, f"{where}: mav_frames.rejects_unsupported", allow_not_applicable=False, basis_required=False)


def check_mav_cmd_definition(doc: dict, errors: list[str], where: str) -> None:
    """Checks the one thing schema/mav_cmd_definition.schema.json can't rule
    out structurally: two params keys sharing the same numeric index prefix."""
    seen_indices: dict[str, str] = {}
    for key in doc.get("params", {}):
        idx = key.split("_", 1)[0]
        if idx in seen_indices:
            errors.append(f"{where}: params keys {seen_indices[idx]!r} and {key!r} share index {idx}")
        seen_indices[idx] = key


def check_command_compatibility(compat: dict, params_obj: dict, vocab: dict, versions: dict, errors: list[str], where: str, mav_frame_names: set[str]) -> None:
    # params_obj comes from the sibling mav_cmd_definition doc (a dict keyed
    # "<index>_<name>") -- see main(). Its own internal consistency (duplicate
    # indices) is checked once via check_mav_cmd_definition when that doc is
    # visited, not repeated here for every context file that references it.
    valid_param_keys = {k for k in params_obj if not k.endswith("_Empty")}
    all_param_keys = set(params_obj)

    for stack, stack_status in compat.items():
        if stack not in vocab["stacks"]:
            errors.append(f"{where}: unknown stack {stack!r} (not in schema/vocab.json)")
            continue
        frames = stack_status.get("frames")
        if frames is False:
            if stack_status.get("basis") not in vocab["basis"]:
                errors.append(f"{where}: {stack}: unknown basis {stack_status.get('basis')!r}")
            impl_url = stack_status.get("impl_url")
            if impl_url is not None and not URL_PATTERN.match(impl_url):
                errors.append(f"{where}: {stack}: impl_url {impl_url!r} is not http(s)")
            check_version_field(
                stack_status.get("last_checked_version"), "last_checked_version",
                stack, versions, errors, f"{where}: {stack}", allow_main=False,
            )
            continue
        if any(k in stack_status for k in ("basis", "notes", "impl_url", "last_checked_version")):
            errors.append(f"{where}: {stack}: basis/notes/impl_url/last_checked_version only valid when frames is false (each frame carries its own)")
        valid_frames = set(vocab["frames"].get(stack, []))
        for frame_name, frame_status in frames.items():
            if frame_name not in valid_frames:
                errors.append(f"{where}: {stack}: unknown frame {frame_name!r} (not in schema/vocab.json frames.{stack})")
                continue
            check_frame_status(frame_status, stack, versions, errors, f"{where}: {stack}.{frame_name}", valid_param_keys, all_param_keys, mav_frame_names)


def main() -> int:
    vocab = load_json(SCHEMA_DIR / "vocab.json")
    versions = load_json(SCHEMA_DIR / "versions.json")
    validators = build_validators()

    mav_frame_path = DATA_DIR / "common" / "enums" / "MAV_FRAME.json"
    mav_frame_names = {v["name"] for v in load_json(mav_frame_path).get("values", [])} if mav_frame_path.exists() else set()

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

        if kind == "definitions":
            check_mav_cmd_definition(doc, errors, str(rel))

        is_command = kind in ("mission", "command")
        compat = doc.get("compatibility")
        if isinstance(compat, dict):
            if is_command:
                def_path = path.parent.parent / "definitions" / path.name
                if def_path.exists():
                    params_obj = load_json(def_path).get("params", {})
                else:
                    errors.append(f"{rel}: missing sibling definitions file {def_path.relative_to(REPO_ROOT)}")
                    params_obj = {}
                check_command_compatibility(compat, params_obj, vocab, versions, errors, f"{rel}: compatibility", mav_frame_names)
            else:
                check_compatibility(compat, vocab, versions, errors, f"{rel}: compatibility", allow_not_applicable=False)

        for sub_key in ("fields", "values"):
            sub_items = doc.get(sub_key, [])
            for i, item in enumerate(sub_items):
                sub_compat = item.get("compatibility")
                if isinstance(sub_compat, dict):
                    check_compatibility(
                        sub_compat, vocab, versions, errors,
                        f"{rel}: {sub_key}[{i}] ({item.get('name')})", allow_not_applicable=False,
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
