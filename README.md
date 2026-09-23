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

Every compatibility fact is a **support statement**, modeled on mdn/browser-compat-data's:

```json
{ "version_added": "≤1.17.0", "basis": "testing", "notes": "..." }
```

- `version_added` (required):
  - `"X.Y.Z"`: the known first version.
  - `"≤X.Y.Z"`: present as of that release, earlier releases not checked.
  - `"main"`: dev branch only.
  - `false`: not supported.
  - `null`: not yet evaluated.
  - `"not-applicable"`: commands only.
- `version_deprecated`, `version_removed`: optional, `"main"` | `"X.Y.Z"`, only valid alongside a real `version_added`. Optional fields are **omitted**, not `null`, when unset.
- `basis`: `"unknown" | "code-inspection" | "testing" | "verified"`. Required on messages/enums and on each command frame's own `supported`; optional beneath a frame, where omitted means "same as the frame's".
- `last_checked_version`: optional, `"X.Y.Z"` only (never `"main"` — it must be a fixed baseline to check staleness against future releases). The release a statement was last confirmed against — recommended on every `false`. Not valid alongside `version_removed`.
- `notes`, `impl_url`: optional. `notes` is a single terse fragment or an array of them (one per distinct fact) — see CLAUDE.md for the terseness rule. `impl_url` is a tracking-issue or PR link.
- Any statement may instead be a **history array**, newest first, with non-overlapping ranges, for an added → removed → re-added timeline:
  ```json
  [ { "version_added": "1.15.0", "basis": "testing" },
    { "version_added": "≤1.11.0", "version_removed": "1.12.0", "basis": "code-inspection" } ]
  ```

Messages and enums key statements by stack, then `default`+variant-override:

```json
{
  "name": "HEARTBEAT",
  "id": 0,
  "dialect": "minimal",
  "compatibility": {
    "ardupilot": {
      "default": { "version_added": "4.3.0", "basis": "code-inspection" },
      "copter":  { "version_added": "4.3.0", "basis": "testing", "notes": "..." }
    },
    "px4": { "default": { "version_added": null, "basis": "unknown" } }
  },
  "fields": [ { "name": "type", "enumRef": "MAV_TYPE", "compatibility": { "...": "..." } } ]
}
```

- `compatibility` is keyed by stack, then `"default"` or a variant name from `schema/vocab.json`'s `frames` list. A variant key overrides `default` for that variant only; no key means "inherits `default`".
- Fields/values carry `compatibility` only where evaluated — absent means unknown, and nothing needs listing just because the parent is known. A field/value may only have a `(stack, variant)` key where the parent entity is implemented there (any entry with a real `version_added`).

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
    "px4": {
      "frames": {
        "multicopter": {
          "supported": { "version_added": "≤1.17.0", "basis": "verified" },
          "nacks_on_non_sentinel_value": { "version_added": false, "last_checked_version": "1.17.0" },
          "params": {
            "3_Flags":    { "supported": { "version_added": false, "last_checked_version": "1.17.0" },
                            "nacks_on_non_sentinel_value": { "version_added": "≤1.17.0" } },
            "7_Altitude": { "supported": { "version_added": "≤1.17.0" } }
          }
        }
      }
    },
    "ardupilot": { "frames": {} }
  }
}
```

- `frames`: `{}` (untested) | `false` (confirmed unsupported on every frame) | an object keyed by frame name (from `schema/vocab.json`'s `frames` list) with **no fallback between frames** — an absent frame is unknown.
- `params` (keyed the same way as the definitions doc's roster) lists only the params actually evaluated — an absent param is unknown. Allowed only once the frame is implemented, and a param's ranges must sit inside the frame's.
- `accept_nan_or_int32max`/`nacks_on_non_sentinel_value`: sentinel-value subfeatures — does a param accept its sentinel (`NaN`/`INT32_MAX`), does it correctly reject a real non-sentinel value while unsupported. Each is its own statement, with its own version range. Set on the frame as the default for every param (as above), overridden per param (`3_Flags`). A param's `nacks_on_non_sentinel_value` range can't overlap its `supported` range.
- A command param named `"Empty"` is a reserved/undocumented upstream slot, not a feature: its `supported` is omitted or `"not-applicable"`, and its entry otherwise exists only for sentinel-subfeature overrides.
- Full field reference and rationale, including `mav_frames`: [CLAUDE.md](CLAUDE.md).

## Scripts

```
pip install -r scripts/requirements.txt

python scripts/generate_stubs.py        # add stubs for new upstream messages/enums/commands
python scripts/validate.py              # validate data/ — run before every PR
python scripts/check_sync.py [--fix]    # report (or fix) drift vs upstream MAVLink XML
```

`check_sync.py` catches structural drift against the upstream MAVLink XML — e.g. a command param that was `"Empty"`/reserved gaining a real name. With `--fix` it patches identity fields (`name`/`enumRef`) or appends new fields/params/values in place (with no `compatibility` — an absent field/value/param entry already means unknown; a new command param only goes into `definitions/`), never touching existing `compatibility` data beyond rewriting a renamed param's key. The one deletion it performs is a command's `mission/` or `command/` doc when upstream's `mission="true"`/`command="true"` attribute no longer allows that context; other removals are only ever reported. `generate_stubs.py` only creates context docs for contexts upstream allows. Runs automatically on a weekly schedule via `.github/workflows/sync-check.yml`, opening a PR with any fixes.

## Contributing

1. Edit the relevant JSON file(s) under `data/`.
2. `python scripts/validate.py` — must pass with no errors.
3. Open a PR — `.github/workflows/validate.yml` re-checks it.

## Validation checklist (`scripts/validate.py`)

- Every file under `data/` is UTF-8 (no byte-order mark), valid JSON, and matches its type's JSON Schema.
- Filename matches the doc's `name`; `dialect`/`context` fields match their directory.
- Every `basis` value present is in `schema/vocab.json` (required on messages/enums and each command frame's own `supported`; optional beneath a frame — omitted means "same as the frame's own `basis`").
- `version_added` is `false`/`null`/`"main"`/`"X.Y.Z"`/`"≤X.Y.Z"` (or `"not-applicable"`, commands only); `version_deprecated`/`version_removed` are `"main"`/a version string, only alongside a real `version_added`, and not before it; `last_checked_version` is a version string only (`"main"` not allowed), and not alongside `version_removed`. Any concrete version (`≤` stripped) exists in `schema/versions.json` for that stack.
- History arrays: every entry has a real `version_added`, ordered newest first, ranges non-overlapping.
- `impl_url`, if present, is a well-formed `http(s)://` URL.
- No duplicate entity files in the same directory.

Messages/enums:
- Every `compatibility` stack key is in `schema/vocab.json`; every variant key is `"default"` or a valid frame name for that stack.
- A field/value only has a `(stack, variant)` key in its `compatibility` where the parent entity is implemented for that variant. Absent is fine (unknown).

Commands (`MAV_CMD`):
- Every `mission/`/`command/` doc has a sibling `definitions/<NAME>.json` with the param roster; no two of its `params` keys share the same `<index>` prefix, and every frame `params` key is in it.
- Every `compatibility` stack key is in `schema/vocab.json`; every `frames` key is a valid frame name for that stack.
- `basis`/`notes`/`impl_url`/`last_checked_version` are only present alongside `frames: false`, never alongside an object `frames`.
- A frame's `params`, sentinel subfeatures and `mav_frames` are only present if the frame is implemented in some range; each param's supported ranges sit inside the frame's (a `≤` frame start doesn't constrain how early).
- A param's `nacks_on_non_sentinel_value` ranges don't overlap its `supported` ranges.
- A param named `"Empty"` (reserved/undocumented upstream) has no `supported` other than `"not-applicable"`.
- `mav_frames.supported`/`default_frame_command_long` values are known `MAV_FRAME` enum names.
