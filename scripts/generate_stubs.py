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
                "compatibility": default_compatibility(stacks),
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
                "compatibility": default_compatibility(stacks),
            }
            for v in enum.values
        ],
    }


def command_stub(cmd: mavlink_xml.Command, context: str, stacks: list[str]) -> dict:
    return {
        "$schema": "../../../../../schema/command.schema.json",
        "name": cmd.name,
        "value": cmd.value,
        "dialect": "common",
        "context": context,
        "compatibility": default_compatibility(stacks),
        "params": [
            {
                "index": p.index,
                "name": p.name,
                "enumRef": p.enum_ref,
                "compatibility": default_compatibility(stacks),
            }
            for p in cmd.params
        ],
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
            for context in ("mission", "command"):
                path = base / "commands" / context / f"{cmd.name}.json"
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
