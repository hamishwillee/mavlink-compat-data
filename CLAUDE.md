# CLAUDE.md

## Layout

- `data/dialects/<minimal|common|standard>/messages/<NAME>.json` — one MAVLink message per file.
- `data/dialects/<minimal|common|standard>/enums/<NAME>.json` — one enum per file. `MAV_CMD` is **not** here — see commands below.
- `data/dialects/common/mav_cmd/mission/<MAV_CMD_NAME>.json` and `.../command/<MAV_CMD_NAME>.json` — each `MAV_CMD_*` entry gets two independent files, one for mission-item usage, one for direct-command usage. `mav_cmd/` only exists under `common` (MAV_CMD is defined there).
- `schema/*.schema.json` — JSON Schema per doc type; `compatibility-entry.schema.json` holds the shared `compatibility`/`supported` definitions, `$ref`'d by the other three.
- `schema/vocab.json` — controlled vocab: `stacks`, `variants` (per stack), `basis`, `dialects`.
- `schema/versions.json` — known released version numbers per stack, used to validate concrete version strings.
- `scripts/mavlink_xml.py` — shared upstream MAVLink XML parser (used by both scripts below).
- `scripts/generate_stubs.py` — creates missing stub files for new upstream entities. Idempotent, additive only.
- `scripts/check_sync.py` — diffs `data/` against fresh upstream XML; `--fix` patches renames/additions in place.
- `scripts/validate.py` — CI validation entrypoint (see README for the checklist).

## `compatibility` field reference

```json
"compatibility": {
  "<stack>": {
    "default": { "supported": ..., "basis": ..., "notes"?: ..., "impl_url"?: ... },
    "<variant>": { ... }
  }
}
```

- `<stack>`: `px4` | `ardupilot` (from `vocab.json`).
- `<variant>`: `"default"` (stack-wide, propagates to every variant with no key of its own) or a variant name from `vocab.json`. A variant key **overrides** `default` for that variant only — no explicit "propagates" marker needed.
- A leaf value is normally one support-statement object; it may be an **array** of them to represent an added→removed→re-added history.
- `supported`:
  - `false` — confirmed not implemented.
  - `null` — not yet evaluated.
  - `"not-applicable"` — **command docs only**: this `MAV_CMD` doesn't apply in this mission/command context for that stack/variant.
  - object — confirmed implemented:
    - `added_version` (required): `true` (implemented, version unknown) | `"main"` (dev branch only) | `"X.Y.Z"`.
    - `deprecated_version` / `removed_version` (optional, **omitted** when not applicable): `"main"` | `"X.Y.Z"`.
    - `partial_implementation` (optional bool, **omitted** when false): present-but-incomplete/WIP as of `added_version`. Describe specifics in `notes`.
- `basis`: `unknown` | `code-inspection` | `testing` | `verified` — orthogonal to `supported`.
- `notes`, `impl_url`: optional, omitted when unset. `impl_url` is a single URL (tracking issue, PR, or doc).
- `last_checked_version` (optional, sibling of `supported`/`basis`/`notes`/`impl_url` — not nested inside `supported`, since `supported` is a bare scalar for `false`/`null`/`"not-applicable"`): `"main"` | `"X.Y.Z"`, the version this statement was last confirmed against. Records when a fact might go stale (most useful on `supported: false` — implemented-ness can change in a later release), not when it was introduced (`added_version`'s job). Recommended whenever `basis` is `code-inspection`/`testing`/`verified` and `supported` is `false`; omit when not tracked.

**Notes are extremely terse** — a fragment, ~10 words, no period. Say only what the structured fields can't. Cut restatements of `supported`/`basis`/`added_version`/`partial_implementation`, hedges, and cross-refs the reader can already see. Name the exception, not the working case: "Alt not used (UseAltitude=false)", not "Only lat/lon-based gating (UseAltitude=false) is implemented — see param compatibility for details." `notes` may be a single string or an **array** of strings — use the array when there are two-plus genuinely distinct facts, one fragment each, rather than fusing them with "; ".

**Omit vs. explicit `null` convention**: omit a field (`notes`, `impl_url`, `deprecated_version`, `removed_version`, `partial_implementation`) when nobody has entered that information yet. Use explicit `null` only for a fact tooling actually determined — e.g. a param's `enumRef: null` means "confirmed: no enum reference," not "unknown."

**Sub-entity (field/param/value) compatibility is gated by its parent.** A field/param/value carries a `compatibility` entry for a given `(stack, variant)` **only if** the parent message/command/enum's effective `supported` there is an implemented object (`true`/version/partial) — resolving `default`→variant inheritance the same way the parent itself does. When the parent's effective status there is `false`, `null`, or `"not-applicable"`, the sub-entity must **omit** that `(stack, variant)` entirely (not write `unknown`) — there's nothing to evaluate until the parent is known. `compatibility` itself is optional on a sub-entity and omitted altogether once nothing qualifies. `scripts/validate.py` enforces both directions: missing-when-required and present-when-not-required are both errors. `generate_stubs.py` never pre-populates sub-entity compatibility on a brand-new entity (its parent always starts unknown); `check_sync.py --fix` only stubs a newly-added sub-entity's compatibility for stacks the parent already confirms implemented.

**A command param named `"Empty"` is exempt, not "unknown."** `scripts/mavlink_xml.py`'s `_param_name()` synthesizes the literal name `"Empty"` for a reserved/undocumented upstream param slot (no `label` attribute in the XML) — that's not a feature any stack could support or fail to support, so it never carries `compatibility`, for any stack, regardless of the command's own status. This is a stronger exemption than the general gating rule above: an ordinary param loses its entry only where the parent isn't implemented, but an `"Empty"` param never gets one at all. `scripts/validate.py` flags one if present. Once upstream assigns it a real label — already caught by `check_sync.py`'s rename detection — it's an ordinary param from that point on, and the normal gating rule (and `validate.py`'s enforcement of it) applies immediately; `check_sync.py --fix` never attaches compatibility when appending a newly-added param still named `"Empty"`.

**Enums referenced by a field/param are not duplicated.** A message field or command param that takes its values from an enum carries `enumRef: "<ENUM_NAME>"` (or `null`). The enum's own value-level compatibility lives once in its own `enums/<ENUM_NAME>.json`.

## ArduPilot versioning

ArduPilot ships separate firmware per vehicle (Copter/Plane/Rover/Sub/Tracker/Blimp) from one monorepo; stable releases land as a roughly coordinated `X.Y` wave but patch-level (`X.Y.Z`) cadence differs per vehicle, and not every vehicle gets every point release. `ardupilot.default` is therefore a **major.minor-level approximation of the shared implementation** (most MAVLink handling lives in `libraries/GCS_MAVLink`), not a claim about one vehicle's exact build. Add a variant override only for a real functional difference (unsupported on that vehicle, a different minor version, partial support) — not to chase per-vehicle patch numbers.

## `schema/versions.json`

Flat list of released `X.Y.Z` numbers per stack (ArduPilot: whole-project numbers, not per-vehicle tags like `Copter-4.5.0`). Maintained by hand — append new releases as they ship. Any concrete version string used in `supported.added_version`/`deprecated_version`/`removed_version`/`last_checked_version` must appear here for its stack.

## Sync drift categories (`scripts/check_sync.py`)

- **New entity upstream** — no stub file yet. Not fixed by this script; run `generate_stubs.py`.
- **Addition/rename within an existing entity** — a `name`/`enumRef` differs from upstream, or upstream declares a field/param/value not yet in the doc. **Auto-fixable** with `--fix`: patches the identity field or appends the new sub-entity, with fresh `unknown` compatibility for whichever stacks the parent already confirms implemented (per the sub-entity gating rule — omitted otherwise); existing `compatibility` data is never touched.
- **Removed upstream** — a field/param/value/entity no longer exists upstream. **Never auto-fixed or deleted** — flagged for manual review, since deleting would destroy compat history.

## Keeping `README.md` in sync

`README.md` duplicates parts of this file for a public-facing audience: the layout tree, the entity doc shape example, the `compatibility` field reference, the scripts list, and the validation checklist. Any change here that changes what one of those says — a new/changed `compatibility` field, a new gating/validation rule, a script's behavior, a new sync-drift category — **must** update the matching part of `README.md` in the same change, not as a follow-up. Check it before calling a schema/script/convention change done, whether or not the user asked for README specifically.

## Working with stub data

- Don't hand-edit the structure `generate_stubs.py`/`check_sync.py` produce (field order, key names, nesting) — only fill in `compatibility`/`notes`/`impl_url` values.
- After editing: `python scripts/validate.py` must pass before opening a PR.
- `python scripts/generate_stubs.py` / `check_sync.py [--fix]` both accept `--cache-dir DIR` (a directory of pre-fetched `<dialect>.xml` files) to avoid network fetches, e.g. for local testing.
