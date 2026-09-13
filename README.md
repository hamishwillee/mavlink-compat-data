# mavlink-compat-data

Structured, machine-validated data on which MAVLink messages/fields/enums/enum-values/commands are implemented by PX4 and ArduPilot (and their vehicle variants) — inspired by [mdn/browser-compat-data](https://github.com/mdn/browser-compat-data).

## Layout

```
data/dialects/<minimal|common|standard>/
  messages/<NAME>.json
  enums/<NAME>.json
  mav_cmd/mission/<MAV_CMD_NAME>.json    # common only — MAV_CMD used as a mission item
  mav_cmd/command/<MAV_CMD_NAME>.json    # common only — MAV_CMD used as a direct command
schema/       # JSON Schema + controlled vocab (vocab.json) + known releases (versions.json)
scripts/      # generation, sync-check, and CI validation
```

One JSON file per message/enum/command. Fields, enum values, and command params are nested arrays/lists inside that file, not separate files.

## Entity doc shape

```json
{
  "name": "HEARTBEAT",
  "id": 0,
  "dialect": "minimal",
  "compatibility": {
    "ardupilot": {
      "default": { "supported": { "added_version": "4.3.0" }, "basis": "code-inspection" },
      "copter":  { "supported": { "added_version": "4.3.0", "partial_implementation": true }, "basis": "testing", "notes": "..." }
    },
    "px4": { "default": { "supported": null, "basis": "unknown" } }
  },
  "fields": [ { "name": "type", "enumRef": "MAV_TYPE", "compatibility": { "...": "..." } } ]
}
```

- `compatibility` is keyed by stack, then `"default"` or a variant. A variant key overrides `default` for that variant only; no key means "inherits `default`".
- `supported`: `false` (not implemented) | `null` (not yet evaluated) | `"not-applicable"` (command docs only) | `{ added_version, deprecated_version?, removed_version?, partial_implementation? }`.
  - `added_version`: `true` (implemented, version unknown) | `"main"` (dev branch only) | `"X.Y.Z"`.
  - Optional fields are **omitted**, not `null`, when unset.
- `basis`: `"unknown" | "code-inspection" | "testing" | "verified"`.
- `last_checked_version`: optional, `"main"` | `"X.Y.Z"` — the version a `false` (or other) statement was last confirmed against, so staleness is checkable later. Distinct from `added_version`.
- `notes`, `impl_url`: optional. `notes` is a single terse fragment or an array of them (one per distinct fact) — see CLAUDE.md for the terseness rule. `impl_url` is a tracking-issue or PR link.
- A field/param/value only carries `compatibility` for a `(stack, variant)` where the parent entity is confirmed implemented there — otherwise it's omitted entirely, not `unknown`.
- Full field reference and rationale: [CLAUDE.md](CLAUDE.md).

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
- Every `compatibility` stack key is in `schema/vocab.json`; every variant key is `"default"` or valid for that stack.
- Every `basis` value is in `schema/vocab.json`.
- `supported` is exactly `false`, `null`, `"not-applicable"`, or a valid object; `"not-applicable"` only in command docs.
- `added_version`/`deprecated_version`/`removed_version`/`last_checked_version` are `true`/`"main"`/a version string as applicable, and any concrete version exists in `schema/versions.json` for that stack.
- `impl_url`, if present, is a well-formed `http(s)://` URL.
- A field/param/value's `compatibility` is present for a `(stack, variant)` if and only if the parent entity is confirmed implemented there — both missing-when-required and present-when-not-required are errors.
- No duplicate entity files in the same directory.
