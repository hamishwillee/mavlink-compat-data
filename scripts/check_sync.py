#!/usr/bin/env python3
"""Diffs data/ against a fresh parse of upstream MAVLink XML to catch drift —
e.g. a command param that was "Empty"/reserved gaining a real name and
meaning in a later MAVLink revision.

Three categories of drift:
  - new_entity:     a message/enum/MAV_CMD exists upstream but has no stub yet.
                     Not fixed here — run generate_stubs.py.
  - addition/rename: a field/value/param was added or renamed on an existing
                     entity (e.g. a reserved param gaining a real name).
                     Safe to auto-fix with --fix: only the identity field
                     (name/enumRef) is patched, or the new sub-entity is
                     appended with fresh "unknown" compatibility for whichever
                     stacks the parent already confirms implemented (omitted
                     entirely otherwise, per CLAUDE.md's sub-entity gating
                     rule) — every existing compatibility block is left
                     untouched.
  - removed/removed_entity: a field/value/param/entity that used to exist
                     upstream no longer does. Never auto-fixed or deleted —
                     flagged for a human to decide (record as removed, or
                     archive), since deleting would destroy compat history.

Usage:
    python scripts/check_sync.py [--cache-dir DIR] [--fix]

Exit code is non-zero if any drift remains unresolved after the run.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import mavlink_xml
from generate_stubs import (
    DATA_DIR,
    REPO_ROOT,
    gated_command_param_stub_targets,
    gated_sub_compatibility,
    load_vocab,
    rename_context_param_references,
    rename_definition_param_key,
)


@dataclass
class Drift:
    category: str
    path: Path
    detail: str
    fixed: bool = False


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def save_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n")


def check_messages(dialect_name: str, messages, stacks: list[str], fix: bool, drifts: list[Drift]) -> None:
    base = DATA_DIR / dialect_name / "messages"
    for msg in messages:
        path = base / f"{msg.name}.json"
        if not path.exists():
            drifts.append(Drift("new_entity", path, f"message {msg.name} has no stub (run generate_stubs.py)"))
            continue

        doc = load_json(path)
        stored_fields = doc.get("fields", [])
        changed = False

        for i, f in enumerate(msg.fields):
            if i >= len(stored_fields):
                drifts.append(Drift("addition", path, f"fields[{i}] {f.name!r} present upstream, missing locally", fixed=fix))
                if fix:
                    new_field = {"name": f.name, "enumRef": f.enum_ref}
                    sub_compat = gated_sub_compatibility(doc.get("compatibility", {}), stacks)
                    if sub_compat:
                        new_field["compatibility"] = sub_compat
                    stored_fields.append(new_field)
                    changed = True
                continue
            sf = stored_fields[i]
            if sf.get("name") != f.name:
                drifts.append(Drift("rename", path, f"fields[{i}].name: {sf.get('name')!r} -> {f.name!r}", fixed=fix))
                if fix:
                    sf["name"] = f.name
                    changed = True
            if sf.get("enumRef") != f.enum_ref:
                drifts.append(Drift("rename", path, f"fields[{i}].enumRef: {sf.get('enumRef')!r} -> {f.enum_ref!r}", fixed=fix))
                if fix:
                    sf["enumRef"] = f.enum_ref
                    changed = True

        for i in range(len(msg.fields), len(stored_fields)):
            drifts.append(Drift("removed", path, f"fields[{i}] {stored_fields[i].get('name')!r} no longer present upstream"))

        if changed:
            doc["fields"] = stored_fields
            save_json(path, doc)


def check_enums(dialect_name: str, enums, stacks: list[str], fix: bool, drifts: list[Drift]) -> None:
    base = DATA_DIR / dialect_name / "enums"
    for enum in enums:
        path = base / f"{enum.name}.json"
        if not path.exists():
            drifts.append(Drift("new_entity", path, f"enum {enum.name} has no stub (run generate_stubs.py)"))
            continue

        doc = load_json(path)
        stored_values = doc.get("values", [])
        stored_by_value = {v["value"]: v for v in stored_values}
        upstream_by_value = {v.value: v for v in enum.values}
        changed = False

        for val, u in upstream_by_value.items():
            if val not in stored_by_value:
                drifts.append(Drift("addition", path, f"values[value={val}] {u.name!r} present upstream, missing locally", fixed=fix))
                if fix:
                    new_value = {"name": u.name, "value": u.value}
                    sub_compat = gated_sub_compatibility(doc.get("compatibility", {}), stacks)
                    if sub_compat:
                        new_value["compatibility"] = sub_compat
                    stored_values.append(new_value)
                    changed = True
            elif stored_by_value[val].get("name") != u.name:
                sv = stored_by_value[val]
                drifts.append(Drift("rename", path, f"values[value={val}].name: {sv.get('name')!r} -> {u.name!r}", fixed=fix))
                if fix:
                    sv["name"] = u.name
                    changed = True

        for val, sv in stored_by_value.items():
            if val not in upstream_by_value:
                drifts.append(Drift("removed", path, f"values[value={val}] {sv.get('name')!r} no longer present upstream"))

        if changed:
            stored_values.sort(key=lambda v: v["value"])
            doc["values"] = stored_values
            save_json(path, doc)


def check_commands(commands, stacks: list[str], fix: bool, drifts: list[Drift]) -> None:
    def_base = DATA_DIR / "common" / "mav_cmd" / "definitions"
    context_bases = {ctx: DATA_DIR / "common" / "mav_cmd" / ctx for ctx in ("mission", "command")}

    for cmd in commands:
        def_path = def_base / f"{cmd.name}.json"
        context_paths = {ctx: base / f"{cmd.name}.json" for ctx, base in context_bases.items()}

        missing = [p for p in (def_path, *context_paths.values()) if not p.exists()]
        if missing:
            for p in missing:
                drifts.append(Drift("new_entity", p, f"command {cmd.name} has no stub here (run generate_stubs.py)"))
            continue

        def_doc = load_json(def_path)
        context_docs = {ctx: load_json(p) for ctx, p in context_paths.items()}

        # Params identity lives once in the shared definitions doc; a rename
        # or addition there must also propagate into whichever of the two
        # context docs' compatibility.<stack>.frames.<frame> blocks already
        # reference that param, since both share the same key format.
        params = def_doc.get("params", {})
        stored_by_index: dict[int, tuple[str, str]] = {}
        for key in params:
            idx_str, _, name = key.partition("_")
            stored_by_index[int(idx_str)] = (key, name)
        upstream_by_index = {p.index: p for p in cmd.params}

        def_changed = False
        context_changed = {ctx: False for ctx in context_docs}

        for idx, u in upstream_by_index.items():
            if idx not in stored_by_index:
                drifts.append(Drift("addition", def_path, f"params[index={idx}] {u.name!r} present upstream, missing locally", fixed=fix))
                if fix:
                    new_key = f"{u.index}_{u.name}"
                    params[new_key] = {"enumRef": u.enum_ref}
                    def_changed = True
                    if u.name != "Empty":
                        # A reserved/undocumented slot never carries compatibility,
                        # regardless of parent status — see CLAUDE.md.
                        for ctx, doc in context_docs.items():
                            for stack, frame_name in gated_command_param_stub_targets(doc.get("compatibility", {})):
                                frame_status = doc["compatibility"][stack]["frames"][frame_name]
                                frame_status.setdefault("params", {})[new_key] = {"supported": None, "basis": "unknown"}
                                context_changed[ctx] = True
                    stored_by_index[idx] = (new_key, u.name)
                continue
            sk, sname = stored_by_index[idx]
            if sname != u.name:
                drifts.append(Drift("rename", def_path, f"params[index={idx}].name: {sname!r} -> {u.name!r}", fixed=fix))
                if fix:
                    rename_definition_param_key(def_doc, idx, sname, u.name)
                    def_changed = True
                    for ctx, doc in context_docs.items():
                        if rename_context_param_references(doc, idx, sname, u.name):
                            context_changed[ctx] = True
                    sk = f"{idx}_{u.name}"
                    stored_by_index[idx] = (sk, u.name)
            sp = params[sk]
            if sp.get("enumRef") != u.enum_ref:
                drifts.append(Drift("rename", def_path, f"params[index={idx}].enumRef: {sp.get('enumRef')!r} -> {u.enum_ref!r}", fixed=fix))
                if fix:
                    sp["enumRef"] = u.enum_ref
                    def_changed = True

        for idx, (sk, sname) in stored_by_index.items():
            if idx not in upstream_by_index:
                drifts.append(Drift("removed", def_path, f"params[index={idx}] {sname!r} no longer present upstream"))

        if def_changed:
            def_doc["params"] = params
            save_json(def_path, def_doc)
        for ctx, changed in context_changed.items():
            if changed:
                save_json(context_paths[ctx], context_docs[ctx])


def check_removed_entities(dialects: dict, drifts: list[Drift]) -> None:
    for dialect_name, dialect in dialects.items():
        base = DATA_DIR / dialect_name
        upstream_messages = {m.name for m in dialect.messages}
        upstream_enums = {e.name for e in dialect.enums}
        for path in sorted((base / "messages").glob("*.json")):
            if path.stem not in upstream_messages:
                drifts.append(Drift("removed_entity", path, f"message {path.stem} no longer defined in {dialect_name}.xml"))
        for path in sorted((base / "enums").glob("*.json")):
            if path.stem not in upstream_enums:
                drifts.append(Drift("removed_entity", path, f"enum {path.stem} no longer defined in {dialect_name}.xml"))

    upstream_commands = {c.name for c in dialects["common"].commands}
    for sub in ("definitions", "mission", "command"):
        base = DATA_DIR / "common" / "mav_cmd" / sub
        if not base.exists():
            continue
        for path in sorted(base.glob("*.json")):
            if path.stem not in upstream_commands:
                drifts.append(Drift("removed_entity", path, f"command {path.stem} no longer defined in MAV_CMD"))


CATEGORY_LABELS = {
    "new_entity": "New entities upstream (run generate_stubs.py to add stubs)",
    "addition": "New fields/params/values on existing entities",
    "rename": "Renamed fields/params/values (e.g. a reserved param gaining a real name)",
    "removed": "Fields/params/values no longer present upstream (needs manual review)",
    "removed_entity": "Entities no longer present upstream (needs manual review)",
}


def report(drifts: list[Drift]) -> None:
    if not drifts:
        print("OK: no drift found between data/ and upstream MAVLink XML.")
        return
    by_category: dict[str, list[Drift]] = {}
    for d in drifts:
        by_category.setdefault(d.category, []).append(d)
    for cat in ("new_entity", "addition", "rename", "removed", "removed_entity"):
        items = by_category.get(cat)
        if not items:
            continue
        print(f"\n{CATEGORY_LABELS[cat]}:")
        for d in items:
            marker = " [fixed]" if d.fixed else ""
            print(f"  {d.path.relative_to(REPO_ROOT)}: {d.detail}{marker}")
    unresolved = sum(1 for d in drifts if not d.fixed)
    print(f"\n{len(drifts)} drift item(s), {unresolved} unresolved.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", type=Path, default=None, help="Directory of pre-fetched dialect XML.")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Apply safe fixes (renames, additions within existing entities). Never deletes files or touches compatibility data.",
    )
    args = parser.parse_args()

    vocab = load_vocab()
    stacks = vocab["stacks"]
    dialects = mavlink_xml.load_all_dialects(args.cache_dir)

    drifts: list[Drift] = []
    for dialect_name, dialect in dialects.items():
        check_messages(dialect_name, dialect.messages, stacks, args.fix, drifts)
        check_enums(dialect_name, dialect.enums, stacks, args.fix, drifts)
        if dialect_name == "common":
            check_commands(dialect.commands, stacks, args.fix, drifts)
    check_removed_entities(dialects, drifts)

    report(drifts)
    sys.exit(1 if any(not d.fixed for d in drifts) else 0)


if __name__ == "__main__":
    main()
