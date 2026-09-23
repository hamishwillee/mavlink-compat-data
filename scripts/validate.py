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
LOWER_BOUND = "≤"
NOT_APPLICABLE = "not-applicable"
SENTINEL_SUBFEATURES = ("accept_nan_or_int32max", "nacks_on_non_sentinel_value")

DOC_TYPES = {
    "messages": "message.schema.json",
    "enums": "enum.schema.json",
    "mission": "command.schema.json",
    "command": "command.schema.json",
    "definitions": "mav_cmd_definition.schema.json",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_data_json(path: Path, errors: list[str]) -> dict | None:
    """Loads a data/ file, reporting (instead of crashing on) anything that
    isn't UTF-8 JSON without a byte-order mark -- RFC 8259 requires UTF-8
    for JSON exchanged between systems, and the data carries literal
    non-ASCII ('≤')."""
    rel = path.relative_to(REPO_ROOT)
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append(f"{rel}: starts with a UTF-8 byte-order mark (save as UTF-8 without BOM)")
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as e:
        errors.append(f"{rel}: not valid UTF-8 ({e.reason} at byte {e.start})")
    except json.JSONDecodeError as e:
        errors.append(f"{rel}: invalid JSON ({e.msg} at line {e.lineno} column {e.colno})")
    return None


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
    val, field_name: str, stack: str, versions: dict, errors: list[str], where: str,
    *, allow_main: bool = True, allow_lower_bound: bool = False,
) -> None:
    """Validates a version-string(-or-'main') field. `allow_main=True` (the
    default) covers version_added/version_deprecated/version_removed, where
    'main' legitimately means "happened on the dev branch, not yet
    released". `allow_main=False` covers last_checked_version, which must be
    a specific released version: its job is to be a fixed baseline
    comparable against future releases ("has a newer release shipped since
    this was checked?"), and 'main' can't serve that purpose since it's a
    moving target -- see schema/compatibility-entry.schema.json's
    releasedVersion def. `allow_lower_bound=True` covers version_added only,
    which may carry a '≤' prefix ("present as of this release, earlier ones
    not checked"). Skips non-string values (version_added's false/null) and
    'not-applicable' -- those are checked by the caller."""
    if not isinstance(val, str) or val == NOT_APPLICABLE:
        return
    if val == "main":
        if not allow_main:
            errors.append(f"{where}: {field_name}='main' is not allowed -- must be a specific released version")
        return
    if val.startswith(LOWER_BOUND):
        if not allow_lower_bound:
            errors.append(f"{where}: {field_name}={val!r}: '{LOWER_BOUND}' prefix only valid on version_added")
            return
    bare = val.removeprefix(LOWER_BOUND)
    if not VERSION_PATTERN.match(bare):
        expected = "a version string" if not allow_main else "'main' or a version string"
        errors.append(f"{where}: {field_name}={val!r} is not {expected}")
    elif bare not in versions.get(stack, []):
        errors.append(f"{where}: {field_name}={val!r} is not a known {stack} release (see schema/versions.json)")


def _entries(statement_or_history) -> list:
    if statement_or_history is None:
        return []
    return statement_or_history if isinstance(statement_or_history, list) else [statement_or_history]


def _version_key(version) -> tuple | None:
    """Sort key for a version_* value: '≤' stripped (a lower bound sorts at its
    stated release), 'main' after every release. None if malformed (already
    reported by the schema/check_version_field) -- callers skip ordering checks
    for it rather than crash."""
    if version == "main":
        return (float("inf"),)
    if not isinstance(version, str):
        return None
    bare = version.removeprefix(LOWER_BOUND)
    if not VERSION_PATTERN.match(bare):
        return None
    parts = [int(p) for p in bare.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def _is_real_version(version_added) -> bool:
    """True for a well-formed 'X.Y.Z', '≤X.Y.Z' or 'main' -- i.e. a statement
    saying "implemented in this range", as opposed to false/null/'not-applicable'."""
    return _version_key(version_added) is not None


def _ranges(statement_or_history) -> list[tuple[tuple, tuple, bool]]:
    """(start, end, start_is_exact) for every entry with a real version_added;
    end is 'infinity' when there's no version_removed."""
    out = []
    for statement in _entries(statement_or_history):
        added = statement.get("version_added")
        if not _is_real_version(added):
            continue
        removed = statement.get("version_removed")
        end = _version_key(removed) if removed else (float("inf"),)
        if end is None:
            continue
        out.append((_version_key(added), end, not added.startswith(LOWER_BOUND)))
    return out


def has_implementation(statement_or_history) -> bool:
    """True iff any entry of a (possibly historical) statement is implemented in
    some range -- the gate for sub-entity/param/subfeature data beneath it."""
    return bool(_ranges(statement_or_history))


def _effective_statement(by_variant: dict, variant: str):
    """Resolves a variant against its stack's "default", per the propagation
    rule: a variant with no key of its own inherits "default"."""
    if variant in by_variant:
        return by_variant[variant]
    return by_variant.get("default")


def check_sub_entity_gating(entity_compat: dict, sub_items: list[dict], sub_key: str, errors: list[str], where: str) -> None:
    """Enforces CLAUDE.md's sub-entity gating rule for fields/values (messages/
    enums): a field/value may carry a compatibility entry for a given (stack,
    variant) only if the parent's effective status there has an implemented
    range. An absent entry just means unknown, so only present-but-not-warranted
    is an error. Only the variant keys the field/value actually writes are
    checked: its inherited "default" simply doesn't apply to a variant where
    the parent overrides to not-implemented. Commands/params are handled
    separately by check_command_compatibility/check_frame_status instead."""
    for i, item in enumerate(sub_items):
        item_label = f"{where}: {sub_key}[{i}] ({item.get('name')!r})"
        for stack, child_by_variant in (item.get("compatibility") or {}).items():
            parent_by_variant = entity_compat.get(stack, {})
            for variant in child_by_variant:
                if not has_implementation(_effective_statement(parent_by_variant, variant)):
                    errors.append(f"{item_label}: has compatibility for {stack}.{variant} but parent is not implemented there (omit it -- absent means unknown)")


def check_statement_or_history(
    statement_or_history, stack: str, vocab: dict, versions: dict, errors: list[str], where: str,
    *, allow_not_applicable: bool, basis_required: bool,
) -> None:
    """Checks one statement or history array -- the single shape shared by
    messages/enums, command frames, params, sentinel subfeatures and
    mav_frames.rejects_unsupported. basis_required=False covers everything
    beneath a command frame's own 'supported', where a missing 'basis' means
    'same basis as the enclosing frame' -- a documentation convention this
    function doesn't resolve, just permits (see CLAUDE.md)."""
    statements = _entries(statement_or_history)
    for statement in statements:
        basis = statement.get("basis")
        if basis is None:
            if basis_required:
                errors.append(f"{where}: missing basis")
        elif basis not in vocab["basis"]:
            errors.append(f"{where}: unknown basis {basis!r}")
        impl_url = statement.get("impl_url")
        if impl_url is not None and not URL_PATTERN.match(impl_url):
            errors.append(f"{where}: impl_url {impl_url!r} is not http(s)")

        added = statement.get("version_added")
        if added == NOT_APPLICABLE and not allow_not_applicable:
            errors.append(f"{where}: version_added 'not-applicable' not valid here")
        check_version_field(added, "version_added", stack, versions, errors, where, allow_lower_bound=True)
        for field_name in ("version_deprecated", "version_removed"):
            val = statement.get(field_name)
            if val is None:
                continue
            if not _is_real_version(added):
                errors.append(f"{where}: {field_name} only valid when version_added is a version")
            check_version_field(val, field_name, stack, versions, errors, where)
        check_version_field(statement.get("last_checked_version"), "last_checked_version", stack, versions, errors, where, allow_main=False)
        if "last_checked_version" in statement and "version_removed" in statement:
            errors.append(f"{where}: last_checked_version not valid alongside version_removed")

        if _is_real_version(added):
            prev_key, prev_name = _version_key(added), "version_added"
            for field_name in ("version_deprecated", "version_removed"):
                val_key = _version_key(statement.get(field_name))
                if val_key is None:
                    continue
                if val_key < prev_key or (field_name == "version_removed" and val_key == _version_key(added)):
                    errors.append(f"{where}: {field_name}={statement[field_name]!r} is not after {prev_name}")
                prev_key, prev_name = val_key, field_name

    if len(statements) > 1:
        if not all(_is_real_version(s.get("version_added")) for s in statements):
            errors.append(f"{where}: every entry of a history array needs a real version_added (not false/null/'not-applicable')")
            return
        for newer, older in zip(statements, statements[1:]):
            removed_key = _version_key(older.get("version_removed"))
            if _version_key(newer["version_added"]) <= _version_key(older["version_added"]):
                errors.append(f"{where}: history array must be ordered newest first ({newer['version_added']!r} before {older['version_added']!r})")
            elif removed_key is None or removed_key > _version_key(newer["version_added"]):
                errors.append(f"{where}: history entry added {older['version_added']!r} overlaps the newer entry added {newer['version_added']!r} (needs an earlier version_removed)")


def check_compatibility(compat: dict, vocab: dict, versions: dict, errors: list[str], where: str) -> None:
    for stack, by_variant in compat.items():
        if stack not in vocab["stacks"]:
            errors.append(f"{where}: unknown stack {stack!r} (not in schema/vocab.json)")
            continue
        valid_variants = {"default", *vocab["frames"].get(stack, [])}
        for variant, statement_or_history in by_variant.items():
            if variant not in valid_variants:
                errors.append(f"{where}: unknown variant {variant!r} for stack {stack!r}")
            check_statement_or_history(
                statement_or_history, stack, vocab, versions, errors, f"{where}: {stack}.{variant}",
                allow_not_applicable=False, basis_required=True,
            )


def _overlaps(a: list, b: list) -> bool:
    return any(a_start < b_end and b_start < a_end for a_start, a_end, _ in a for b_start, b_end, _ in b)


def _within(inner: list, outer: list) -> bool:
    """True iff every inner range sits inside some outer range. An outer range
    whose start is only a lower bound ('≤X') doesn't constrain the start --
    earlier releases weren't checked, so an earlier inner start isn't a
    contradiction."""
    return all(
        any((not exact or o_start <= i_start) and i_end <= o_end for o_start, o_end, exact in outer)
        for i_start, i_end, _ in inner
    )


def check_param_status(key: str, pstat: dict, frame_ranges: list, stack: str, vocab: dict, versions: dict, errors: list[str], where: str) -> None:
    supported = pstat.get("supported")
    check_statement_or_history(supported, stack, vocab, versions, errors, f"{where}.supported", allow_not_applicable=True, basis_required=False)
    for sub_key in SENTINEL_SUBFEATURES:
        check_statement_or_history(pstat.get(sub_key), stack, vocab, versions, errors, f"{where}.{sub_key}", allow_not_applicable=False, basis_required=False)

    if key.endswith("_Empty") and any(s.get("version_added") != NOT_APPLICABLE for s in _entries(supported)):
        errors.append(f"{where}: reserved param ('Empty') can only have supported.version_added 'not-applicable'")
    supported_ranges = _ranges(supported)
    if _overlaps(supported_ranges, _ranges(pstat.get("nacks_on_non_sentinel_value"))):
        errors.append(f"{where}: nacks_on_non_sentinel_value range overlaps a range where the param is supported")
    if not _within(supported_ranges, frame_ranges):
        errors.append(f"{where}: supported range falls outside every range where the frame itself is supported")


def check_frame_status(frame: dict, stack: str, vocab: dict, versions: dict, errors: list[str], where: str, all_param_keys: set[str], mav_frame_names: set[str]) -> None:
    supported = frame.get("supported")
    check_statement_or_history(supported, stack, vocab, versions, errors, f"{where}.supported", allow_not_applicable=True, basis_required=True)
    frame_ranges = _ranges(supported)
    frame_implemented = bool(frame_ranges)

    for sub_key in (*SENTINEL_SUBFEATURES, "params", "mav_frames"):
        if sub_key in frame and not frame_implemented:
            errors.append(f"{where}: has {sub_key} but frame is not supported in any version")
    for sub_key in SENTINEL_SUBFEATURES:
        check_statement_or_history(frame.get(sub_key), stack, vocab, versions, errors, f"{where}.{sub_key}", allow_not_applicable=False, basis_required=False)

    for key, pstat in (frame.get("params") or {}).items():
        if key not in all_param_keys:
            errors.append(f"{where}: params key {key!r} does not match a current param")
        check_param_status(key, pstat, frame_ranges, stack, vocab, versions, errors, f"{where}.params.{key}")

    mav_frames = frame.get("mav_frames")
    if mav_frames is not None:
        for name in mav_frames.get("supported", []):
            if name not in mav_frame_names:
                errors.append(f"{where}: mav_frames.supported {name!r} is not a known MAV_FRAME value")
        default_frame = mav_frames.get("default_frame_command_long")
        if default_frame is not None and default_frame not in mav_frame_names:
            errors.append(f"{where}: mav_frames.default_frame_command_long {default_frame!r} is not a known MAV_FRAME value")
        check_statement_or_history(
            mav_frames.get("rejects_unsupported"), stack, vocab, versions, errors, f"{where}: mav_frames.rejects_unsupported",
            allow_not_applicable=False, basis_required=False,
        )


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
            check_frame_status(frame_status, stack, vocab, versions, errors, f"{where}: {stack}.{frame_name}", all_param_keys, mav_frame_names)


def main() -> int:
    vocab = load_json(SCHEMA_DIR / "vocab.json")
    versions = load_json(SCHEMA_DIR / "versions.json")
    validators = build_validators()

    mav_frame_path = DATA_DIR / "common" / "enums" / "MAV_FRAME.json"
    mav_frame_doc = load_data_json(mav_frame_path, []) if mav_frame_path.exists() else None
    mav_frame_names = {v["name"] for v in (mav_frame_doc or {}).get("values", [])}

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

        doc = load_data_json(path, errors)
        if doc is None:
            continue
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
                    # a load error here is reported when the definitions file itself is visited
                    params_obj = (load_data_json(def_path, []) or {}).get("params", {})
                else:
                    errors.append(f"{rel}: missing sibling definitions file {def_path.relative_to(REPO_ROOT)}")
                    params_obj = {}
                check_command_compatibility(compat, params_obj, vocab, versions, errors, f"{rel}: compatibility", mav_frame_names)
            else:
                check_compatibility(compat, vocab, versions, errors, f"{rel}: compatibility")

        for sub_key in ("fields", "values"):
            sub_items = doc.get(sub_key, [])
            for i, item in enumerate(sub_items):
                sub_compat = item.get("compatibility")
                if isinstance(sub_compat, dict):
                    check_compatibility(
                        sub_compat, vocab, versions, errors,
                        f"{rel}: {sub_key}[{i}] ({item.get('name')})",
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
