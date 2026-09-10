"""Parses upstream MAVLink dialect XML into a normalized model.

Shared by generate_stubs.py and check_sync.py so both work from one parse.
"""
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

UPSTREAM_BASE = "https://raw.githubusercontent.com/mavlink/mavlink/master/message_definitions/v1.0"
DIALECTS = ("minimal", "common", "standard")


@dataclass
class Field:
    name: str
    enum_ref: str | None


@dataclass
class Message:
    name: str
    id: int
    fields: list[Field] = field(default_factory=list)


@dataclass
class EnumValue:
    name: str
    value: int


@dataclass
class Enum:
    name: str
    values: list[EnumValue] = field(default_factory=list)


@dataclass
class Param:
    index: int
    name: str
    enum_ref: str | None


@dataclass
class Command:
    name: str
    value: int
    params: list[Param] = field(default_factory=list)


@dataclass
class Dialect:
    name: str
    messages: list[Message] = field(default_factory=list)
    enums: list[Enum] = field(default_factory=list)       # excludes MAV_CMD
    commands: list[Command] = field(default_factory=list)  # MAV_CMD entries; only non-empty for "common"


def fetch_xml(dialect: str, cache_dir: Path | None = None) -> str:
    """Return raw XML text for a dialect, preferring a cached copy under cache_dir if present."""
    if cache_dir is not None:
        cached = cache_dir / f"{dialect}.xml"
        if cached.exists():
            return cached.read_text()
    url = f"{UPSTREAM_BASE}/{dialect}.xml"
    with urllib.request.urlopen(url, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{dialect}.xml").write_text(text)
    return text


def _param_name(param_el: ET.Element) -> str:
    """A param's display name: its label attribute, or its text content (e.g. "Empty" for a
    reserved/undocumented slot) when there's no label yet."""
    label = param_el.get("label")
    if label:
        return label
    text = (param_el.text or "").strip()
    return text or "Empty"


def parse_dialect(name: str, xml_text: str) -> Dialect:
    root = ET.fromstring(xml_text)

    messages = [
        Message(
            name=msg_el.get("name"),
            id=int(msg_el.get("id")),
            fields=[
                Field(name=f.get("name"), enum_ref=f.get("enum"))
                for f in msg_el.findall("field")
            ],
        )
        for msg_el in root.findall("./messages/message")
    ]

    enums: list[Enum] = []
    commands: list[Command] = []
    for enum_el in root.findall("./enums/enum"):
        enum_name = enum_el.get("name")
        if enum_name == "MAV_CMD":
            for entry_el in enum_el.findall("entry"):
                params = [
                    Param(
                        index=int(p.get("index")),
                        name=_param_name(p),
                        enum_ref=p.get("enum"),
                    )
                    for p in entry_el.findall("param")
                ]
                commands.append(
                    Command(
                        name=entry_el.get("name"),
                        value=int(entry_el.get("value")),
                        params=params,
                    )
                )
            continue
        enums.append(
            Enum(
                name=enum_name,
                values=[
                    EnumValue(name=e.get("name"), value=int(e.get("value")))
                    for e in enum_el.findall("entry")
                ],
            )
        )

    return Dialect(name=name, messages=messages, enums=enums, commands=commands)


def load_dialect(name: str, cache_dir: Path | None = None) -> Dialect:
    return parse_dialect(name, fetch_xml(name, cache_dir))


def load_all_dialects(cache_dir: Path | None = None) -> dict[str, Dialect]:
    return {name: load_dialect(name, cache_dir) for name in DIALECTS}
