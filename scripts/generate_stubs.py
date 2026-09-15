#!/usr/bin/env python3
"""Generates "unknown" stub JSON docs for every message/enum/command in the
minimal/common/standard MAVLink dialects, from upstream MAVLink XML.

Idempotent: only creates files that don't already exist. Never overwrites or
deletes an existing file, so populated compatibility data is never touched.

Usage:
    python scripts/generate_stubs.py [--cache-dir DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import mavlink_xml

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "dialects"
SCHEMA_DIR = REPO_ROOT / "schema"


def load_vocab() -> dict:
    return json.loads((SCHEMA_DIR / "vocab.json").read_text())


def default_compatibility(stacks: list[str]) -> dict:
    return {stack: {"default": {"supported": None, "basis": "unknown"}} for stack in stacks}


def _is_implemented(statement) -> bool:
    """True iff a support-statement (or the last entry of a history array) is
    a confirmed-implemented object, as opposed to false/null/not-applicable."""
    if isinstance(statement, list):
        statement = statement[-1] if statement else None
    return isinstance(statement, dict) and isinstance(statement.get("supported"), dict)


def is_stack_implemented(entity_compat: dict, stack: str) -> bool:
    """True iff entity_compat's *default* variant for this stack is confirmed
    implemented. Used to decide whether a sub-entity (field/param/value) may
    carry its own compatibility for this stack — see CLAUDE.md's sub-entity
    gating rule. Only looks at "default"; deliberate variant overrides are a
    human judgment call made when filling in the data, not something the
    generators infer."""
    return _is_implemented((entity_compat.get(stack) or {}).get("default"))


def gated_sub_compatibility(entity_compat: dict, stacks: list[str]) -> dict:
    """default_compatibility(), filtered to stacks the parent already confirms
    implemented. A sub-entity gets no entry (and no "compatibility" key at
    all, if this comes back empty) for a stack the parent hasn't confirmed —
    there's nothing to evaluate yet."""
    return {stack: {"default": {"supported": None, "basis": "unknown"}} for stack in stacks if is_stack_implemented(entity_compat, stack)}


def command_default_compatibility(stacks: list[str]) -> dict:
    """Every command stack starts fully untested -- frames: {} -- since there's
    no per-frame FrameStatus to write yet for a brand-new command."""
    return {stack: {"frames": {}} for stack in stacks}


def is_frame_implemented(frame_status: dict | None) -> bool:
    return isinstance((frame_status or {}).get("supported"), dict)


def gated_command_param_stub_targets(entity_compat: dict) -> list[tuple[str, str]]:
    """(stack, frame_name) pairs where the frame is already confirmed
    implemented -- i.e. where a newly-discovered param must be stubbed into
    frames.<frame>.params. A stack with frames == {} or frames == False
    contributes nothing (no per-frame breakdown to attach to); a frame not yet
    mentioned under an object 'frames' contributes nothing either — absence
    always means untested, never inherited."""
    targets = []
    for stack, stack_status in (entity_compat or {}).items():
        frames = (stack_status or {}).get("frames")
        if isinstance(frames, dict):
            targets += [(stack, f) for f, fs in frames.items() if is_frame_implemented(fs)]
    return targets


def rename_definition_param_key(definition_doc: dict, index: int, old_name: str, new_name: str) -> bool:
    """Rewrites '<index>_<old_name>' -> '<index>_<new_name>' in the shared
    mav_cmd_definition doc's own params roster. Returns True iff it changed."""
    old_key, new_key = f"{index}_{old_name}", f"{index}_{new_name}"
    params = definition_doc.get("params", {})
    if old_key not in params:
        return False
    params[new_key] = params.pop(old_key)
    return True


def rename_context_param_references(context_doc: dict, index: int, old_name: str, new_name: str) -> bool:
    """Rewrites '<index>_<old_name>' -> '<index>_<new_name>' everywhere it's
    referenced in one mission/command compatibility doc: every
    compatibility.<stack>.frames.<frame>.params map. (The doc carries no
    identity of its own to rename -- that lives in the shared definitions
    doc, see rename_definition_param_key.) Returns True iff anything
    changed."""
    old_key, new_key = f"{index}_{old_name}", f"{index}_{new_name}"
    changed = False
    for stack_status in context_doc.get("compatibility", {}).values():
        frames = (stack_status or {}).get("frames")
        if not isinstance(frames, dict):
            continue
        for frame_status in frames.values():
            params = (frame_status or {}).get("params")
            if isinstance(params, dict) and old_key in params:
                params[new_key] = params.pop(old_key)
                changed = True
    return changed


def message_stub(msg: mavlink_xml.Message, dialect: str, stacks: list[str]) -> dict:
    return {
        "$schema": "../../../../schema/message.schema.json",
        "name": msg.name,
        "id": msg.id,
        "dialect": dialect,
        "compatibility": default_compatibility(stacks),
        "fields": [
            {
                "name": f.name,
                "enumRef": f.enum_ref,
            }
            for f in msg.fields
        ],
    }


def enum_stub(enum: mavlink_xml.Enum, dialect: str, stacks: list[str]) -> dict:
    return {
        "$schema": "../../../../schema/enum.schema.json",
        "name": enum.name,
        "dialect": dialect,
        "compatibility": default_compatibility(stacks),
        "values": [
            {
                "name": v.name,
                "value": v.value,
            }
            for v in enum.values
        ],
    }


def command_definition_stub(cmd: mavlink_xml.Command) -> dict:
    """The static, purely upstream-derived identity shared by a command's
    mission/ and command/ compatibility docs -- see
    schema/mav_cmd_definition.schema.json."""
    return {
        "$schema": "../../../../../schema/mav_cmd_definition.schema.json",
        "name": cmd.name,
        "value": cmd.value,
        "dialect": "common",
        "params": {
            f"{p.index}_{p.name}": {"enumRef": p.enum_ref}
            for p in cmd.params
        },
    }


def command_stub(cmd: mavlink_xml.Command, context: str, stacks: list[str]) -> dict:
    return {
        "$schema": "../../../../../schema/command.schema.json",
        "name": cmd.name,
        "dialect": "common",
        "context": context,
        "compatibility": command_default_compatibility(stacks),
    }


def write_if_missing(path: Path, doc: dict, dry_run: bool) -> bool:
    """Returns True if a file was (or would be) created."""
    if path.exists():
        return False
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n")
    return True


def generate(cache_dir: Path | None, dry_run: bool) -> None:
    vocab = load_vocab()
    stacks = vocab["stacks"]
    created = 0
    skipped = 0

    for dialect_name in mavlink_xml.DIALECTS:
        dialect = mavlink_xml.load_dialect(dialect_name, cache_dir)
        base = DATA_DIR / dialect_name

        for msg in dialect.messages:
            path = base / "messages" / f"{msg.name}.json"
            if write_if_missing(path, message_stub(msg, dialect_name, stacks), dry_run):
                created += 1
            else:
                skipped += 1

        for enum in dialect.enums:
            path = base / "enums" / f"{enum.name}.json"
            if write_if_missing(path, enum_stub(enum, dialect_name, stacks), dry_run):
                created += 1
            else:
                skipped += 1

        for cmd in dialect.commands:
            def_path = base / "mav_cmd" / "definitions" / f"{cmd.name}.json"
            if write_if_missing(def_path, command_definition_stub(cmd), dry_run):
                created += 1
            else:
                skipped += 1
            for context in ("mission", "command"):
                path = base / "mav_cmd" / context / f"{cmd.name}.json"
                if write_if_missing(path, command_stub(cmd, context, stacks), dry_run):
                    created += 1
                else:
                    skipped += 1

    verb = "Would create" if dry_run else "Created"
    print(f"{verb} {created} file(s), skipped {skipped} existing file(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Directory of pre-fetched dialect XML (dialect.xml files); avoids network fetches when present.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would be created without writing files.")
    args = parser.parse_args()
    generate(args.cache_dir, args.dry_run)


if __name__ == "__main__":
    main()
