# mavlink-compat-data

Structured, machine-validated data on which MAVLink messages/fields/enums/enum-values/commands are implemented by PX4 and ArduPilot (and their vehicle variants) — inspired by [mdn/browser-compat-data](https://github.com/mdn/browser-compat-data).

## Layout

```
data/dialects/<minimal|common|standard>/
  messages/<NAME>.json
  enums/<NAME>.json
  mav_cmd/definitions/<MAV_CMD_NAME>.json  # common only — static identity (value, params), no compat data
  mav_cmd/mission/<MAV_CMD_NAME>.json      # common only — MAV_CMD used as a mission item
  mav_cmd/command/<MAV_CMD_NAME>.json      # common only — MAV_CMD used as a direct command
schema/       # JSON Schema + controlled vocab (vocab.json) + known releases (versions.json)
scripts/      # generation, sync-check, and CI validation
```

One JSON file per message/enum/command. Fields and enum values are nested arrays inside that file, not separate files. Commands split identity from compatibility: a shared `definitions/<NAME>.json` holds the static `value`/`params` roster (identical between mission and command usage, so not duplicated), while `mission/<NAME>.json` and `command/<NAME>.json` hold only compatibility data.

## Entity doc shape

Messages and enums use a `default`+variant-override shape:

```json
{
  "name": "HEARTBEAT",
  "id": 0,
  "dialect": "minimal",
  "compatibility": {
    "ardupilot": {
      "default": { "supported": { "added_version": "4.3.0" }, "basis": "code-inspection" },
      "copter":  { "supported": { "added_version": "4.3.0" }, "basis": "testing", "notes": "..." }
    },
    "px4": { "default": { "supported": null, "basis": "unknown" } }
  },
  "fields": [ { "name": "type", "enumRef": "MAV_TYPE", "compatibility": { "...": "..." } } ]
}
```

- `compatibility` is keyed by stack, then `"default"` or a variant name from `schema/vocab.json`'s `frames` list. A variant key overrides `default` for that variant only; no key means "inherits `default`".
- `supported`: `false` (not implemented) | `null` (not yet evaluated) | `{ added_version, deprecated_version?, removed_version? }`.
  - `added_version`: `true` (implemented, version unknown) | `"main"` (dev branch only) | `"X.Y.Z"`.
  - Optional fields are **omitted**, not `null`, when unset.
- `basis`: `"unknown" | "code-inspection" | "testing" | "verified"`.
- `last_checked_version`: optional, `"X.Y.Z"` only (never `"main"` — it must be a fixed baseline to check staleness against future releases) — the version a `false` (or other) statement was last confirmed against. Distinct from `added_version`.
- `notes`, `impl_url`: optional. `notes` is a single terse fragment or an array of them (one per distinct fact) — see CLAUDE.md for the terseness rule. `impl_url` is a tracking-issue or PR link.
- A field/value only carries `compatibility` for a `(stack, variant)` where the parent entity is confirmed implemented there — otherwise it's omitted entirely, not `unknown`.

Commands (`MAV_CMD`) use a per-vehicle-frame shape instead — testing happens incrementally per vehicle, so there's no `default` fallback. Identity (`value`, `params`) lives once in a shared `definitions/<NAME>.json`, since it's purely upstream-derived and identical between mission and command usage:

```json
// mav_cmd/definitions/MAV_CMD_NAV_TAKEOFF.json
{
  "name": "MAV_CMD_NAV_TAKEOFF",
  "value": 22,
  "dialect": "common",
  "params": { "1_Pitch": { "enumRef": null } }
}
```
```json
// mav_cmd/mission/MAV_CMD_NAV_TAKEOFF.json
{
  "name": "MAV_CMD_NAV_TAKEOFF",
  "dialect": "common",
  "context": "mission",
  "compatibility": {
    "ardupilot": {
      "frames": {
        "copter": { "supported": { "added_version": true }, "basis": "verified", "params": { "1_Pitch": { "supported": false } } },
        "plane":  { "supported": { "added_version": true }, "basis": "verified", "params": { "1_Pitch": { "supported": { "added_version": true } } } }
      }
    },
    "px4": { "frames": {} }
  }
}
```

A param's own `basis` is optional (as above, omitted on `1_Pitch` in both frames) — it inherits the enclosing frame's `basis`; write one only when it genuinely differs.

- `frames`: `{}` (untested) | `false` (confirmed unsupported on every frame) | an object keyed by frame name (from `schema/vocab.json`'s `frames` list) with **no fallback between frames** — every known frame is written out in full.
- A frame's own `params.<index>_<name>` (keyed the same way as the definitions doc's roster) may exist only if that frame's `supported` is confirmed-implemented, and then every non-`"Empty"` param must appear.
- A command param named `"Empty"` is a reserved/undocumented upstream slot, not a feature — it still gets an entry in `definitions/<NAME>.json`, but its key never appears in any frame's `params`, for any stack, until upstream gives it a real name.
- Full field reference and rationale, including `sentinel_compliance`/`mav_frames`/`earliest_checked_version`: [CLAUDE.md](CLAUDE.md).

## Scripts

```
pip install -r scripts/requirements.txt

python scripts/generate_stubs.py        # add stubs for new upstream messages/enums/commands
python scripts/validate.py              # validate data/ — run before every PR
python scripts/check_sync.py [--fix]    # report (or fix) drift vs upstream MAVLink XML
```

`check_sync.py` catches structural drift against the upstream MAVLink XML — e.g. a command param that was `"Empty"`/reserved gaining a real name. With `--fix` it patches identity fields (`name`/`enumRef`) or appends new fields/params/values in place (with fresh `compatibility` only for stacks the parent already confirms implemented, per the gating rule above), never touching existing `compatibility` data, and never deletes anything (removals are only ever reported). Runs automatically on a weekly schedule via `.github/workflows/sync-check.yml`, opening a PR with any fixes.

## Contributing

1. Edit the relevant JSON file(s) under `data/`.
2. `python scripts/validate.py` — must pass with no errors.
3. Open a PR — `.github/workflows/validate.yml` re-checks it.

## Validation checklist (`scripts/validate.py`)

- Every file under `data/` is valid JSON and matches its type's JSON Schema.
- Filename matches the doc's `name`; `dialect`/`context` fields match their directory.
- Every `basis` value present is in `schema/vocab.json` (required everywhere except inside a command frame's `params`/`sentinel_compliance`/`mav_frames.rejects_unsupported`, where it's optional — omitted means "same as the frame's own `basis`").
- `added_version`/`deprecated_version`/`removed_version` are `true`/`"main"`/a version string as applicable; `last_checked_version`/`earliest_checked_version` are a version string only (`"main"` not allowed there). Any concrete version exists in `schema/versions.json` for that stack.
- `impl_url`, if present, is a well-formed `http(s)://` URL.
- No duplicate entity files in the same directory.

Messages/enums:
- Every `compatibility` stack key is in `schema/vocab.json`; every variant key is `"default"` or a valid frame name for that stack.
- `supported` is exactly `false`, `null`, or a valid object.
- A field/value's `compatibility` is present for a `(stack, variant)` if and only if the parent entity is confirmed implemented there — both missing-when-required and present-when-not-required are errors.

Commands (`MAV_CMD`):
- Every `mission/`/`command/` doc has a sibling `definitions/<NAME>.json` with the param roster; no two of its `params` keys share the same `<index>` prefix.
- Every `compatibility` stack key is in `schema/vocab.json`; every `frames` key is a valid frame name for that stack.
- `basis`/`notes`/`impl_url`/`last_checked_version` are only present alongside `frames: false`, never alongside an object `frames`.
- `supported` (frame's own, or a param's) is exactly `false`, `null`, `"not-applicable"`, or a valid object.
- A frame's `params`/`sentinel_compliance` entries are present for a param if and only if that frame's own `supported` is confirmed implemented — same both-directions check as messages/enums, applied within one frame, cross-referencing the sibling `definitions/<NAME>.json` for the valid param set.
- `mav_frames.supported`/`default_frame_command_long` values are known `MAV_FRAME` enum names.
- A param named `"Empty"` (reserved/undocumented upstream) never appears as a key in any frame's `params` — but it may appear in `sentinel_compliance`, since a reserved slot's only legal value is the sentinel.
