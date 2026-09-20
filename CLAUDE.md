# CLAUDE.md

## Layout

- `data/dialects/<minimal|common|standard>/messages/<NAME>.json` — one MAVLink message per file.
- `data/dialects/<minimal|common|standard>/enums/<NAME>.json` — one enum per file. `MAV_CMD` is **not** here — see commands below.
- `data/dialects/common/mav_cmd/definitions/<MAV_CMD_NAME>.json` — one shared, purely upstream-derived identity file per `MAV_CMD_*` entry: `value` and the `params` roster (index/name/enumRef). Holds no compatibility data and no human-entered content at all — see below.
- `data/dialects/common/mav_cmd/mission/<MAV_CMD_NAME>.json` and `.../command/<MAV_CMD_NAME>.json` — each `MAV_CMD_*` entry additionally gets two independent compatibility-only files, one for mission-item usage, one for direct-command usage, both referencing the one shared `definitions/<NAME>.json` for identity. `mav_cmd/` only exists under `common` (MAV_CMD is defined there).
- `schema/*.schema.json` — JSON Schema per doc type; `compatibility-entry.schema.json` holds the shared `compatibility`/`supported` definitions, `$ref`'d by the others; `mav_cmd_definition.schema.json` is the schema for the `definitions/` files above.
- `schema/vocab.json` — controlled vocab: `stacks`, `frames` (per stack), `basis`, `dialects`.
- `schema/versions.json` — known released version numbers per stack, used to validate concrete version strings.
- `scripts/mavlink_xml.py` — shared upstream MAVLink XML parser (used by both scripts below).
- `scripts/generate_stubs.py` — creates missing stub files for new upstream entities. Idempotent, additive only.
- `scripts/check_sync.py` — diffs `data/` against fresh upstream XML; `--fix` patches renames/additions in place.
- `scripts/validate.py` — CI validation entrypoint (see README for the checklist).

## `compatibility` field reference: messages and enums

```json
"compatibility": {
  "<stack>": {
    "default": { "supported": ..., "basis": ..., "notes"?: ..., "impl_url"?: ... },
    "<variant>": { ... }
  }
}
```

- `<stack>`: `px4` | `ardupilot` (from `vocab.json`).
- `<variant>`: `"default"` (stack-wide, propagates to every variant with no key of its own) or a frame name from `vocab.json`'s `frames` list for that stack. A variant key **overrides** `default` for that variant only — no explicit "propagates" marker needed.
- A leaf value is normally one support-statement object; it may be an **array** of them to represent an added→removed→re-added history.
- `supported`:
  - `false` — confirmed not implemented.
  - `null` — not yet evaluated.
  - object — confirmed implemented:
    - `added_version` (required): `true` (implemented, version unknown) | `"main"` (dev branch only) | `"X.Y.Z"`.
    - `deprecated_version` / `removed_version` (optional, **omitted** when not applicable): `"main"` | `"X.Y.Z"`.
- `basis`: `unknown` | `code-inspection` | `testing` | `verified` | `autopilot_docs` (stated by the stack's own documentation; weaker than `code-inspection`, carries no version evidence) — orthogonal to `supported`. Where a stack's docs conflict with test results, the docs value is recorded and the test result goes in `notes`.
- `notes`, `impl_url`: optional, omitted when unset. `impl_url` is a single URL (tracking issue, PR, or doc).
- `last_checked_version` (optional, sibling of `supported`/`basis`/`notes`/`impl_url` — not nested inside `supported`, since `supported` is a bare scalar for `false`/`null`): **`"X.Y.Z"` only — never `"main"`** (`schema/compatibility-entry.schema.json`'s `releasedVersion` def, distinct from `added_version`/`deprecated_version`/`removed_version`'s `versionOrMain`; enforced by both the schema and `scripts/validate.py`'s `check_version_field(..., allow_main=False)`). Its job is to be a fixed baseline comparable against `schema/versions.json`'s release list to decide "has a newer release shipped since this was checked, and does it need re-verification?" — `"main"` is a moving target and can't serve that purpose, unlike `added_version` etc. where "happened on the dev branch" is itself a meaningful fact. Records when a fact might go stale (most useful on `supported: false` — implemented-ness can change in a later release), not when it was introduced (`added_version`'s job). Recommended whenever `basis` is `code-inspection`/`testing`/`verified` and `supported` is `false`; omit when not tracked, and don't set it from a dev/pre-release build's testing — wait for an actual tagged release.

**Sub-entity (field/value) compatibility is gated by its parent.** A field/value carries a `compatibility` entry for a given `(stack, variant)` **only if** the parent message/enum's effective `supported` there is an implemented object — resolving `default`→variant inheritance the same way the parent itself does. When the parent's effective status there is `false` or `null`, the sub-entity must **omit** that `(stack, variant)` entirely (not write `unknown`) — there's nothing to evaluate until the parent is known. `compatibility` itself is optional on a sub-entity and omitted altogether once nothing qualifies. `scripts/validate.py` enforces both directions: missing-when-required and present-when-not-required are both errors. `generate_stubs.py` never pre-populates sub-entity compatibility on a brand-new entity (its parent always starts unknown); `check_sync.py --fix` only stubs a newly-added sub-entity's compatibility for stacks the parent already confirms implemented.

## `compatibility` field reference: commands (`MAV_CMD`)

Commands use a different, per-vehicle-frame shape instead of `default`+variant-override — testing happens incrementally per vehicle (multicopter first, then fixed-wing, etc.), frames can support genuinely different combinations of params, and there's no assumed inheritance between frames.

```json
"compatibility": {
  "<stack>": {
    "frames": {},                              // untested -- nothing evaluated for any frame yet
    "basis"?: ..., "notes"?: ..., "impl_url"?: ..., "last_checked_version"?: ...
  }
}
```
```json
"compatibility": {
  "<stack>": {
    "frames": false,                           // confirmed unsupported on every frame (blanket fact)
    "basis": "...", "last_checked_version"?: "..."
  }
}
```
```json
"compatibility": {
  "<stack>": {
    "frames": {
      "<frame>": {
        "supported": ..., "basis": ..., "notes"?: ..., "impl_url"?: ...,
        "last_checked_version"?: ..., "earliest_checked_version"?: ...,
        "params"?: { "<index>_<name>": { "supported": ..., "basis": ..., ... } },
        "mav_frames"?: { "supported"?: ["MAV_FRAME_..."], "rejects_unsupported"?: { ... }, "default_frame_command_long"?: "MAV_FRAME_..." }
      }
    }
  }
}
```

- `<frame>`: a name from `vocab.json`'s `frames` list for that stack (e.g. `plane`, `standard_quadplane`, `copter` for ArduPilot; `multicopter`, `fixedwing`, `vtol` for PX4). **No `default` fallback within `frames`** — every known/tested frame is written out in full, even when two frames share an identical result; absence of a frame key always means untested/unknown, nothing is ever inherited.
- `supported` (a frame's own, or a param's within it): `false` | `null` | `"not-applicable"` (this `MAV_CMD` doesn't apply in this mission/command wire context for that stack, or a specific param doesn't apply within an otherwise-implemented frame) | `{ added_version, deprecated_version?, removed_version? }` — same union as messages/enums, minus `partial_implementation` (removed everywhere; infer partial support from the frame's own `params` breakdown instead).
- `basis`/`notes`/`impl_url`/`last_checked_version` are only valid as siblings of `frames` when `frames` is `false` (describing that blanket claim); once `frames` is an object, each frame carries its own instead.
- Param identity (`{"<index>_<name>": {"enumRef": ...}}`) lives once in the sibling `mav_cmd_definition.schema.json` doc (`data/dialects/common/mav_cmd/definitions/<NAME>.json`), not in the compatibility doc itself — it's purely upstream-derived and identical between a command's `mission` and `command` context files, so duplicating it in both would just be two copies of the same static fact. A frame's own `params.<index>_<name>` may exist **iff** that frame's `supported` is a confirmed-implemented object, and then every non-`"Empty"` param from the definitions doc must appear — same gating spirit as the message/enum rule above, just internal to one frame instead of cross-referencing a separate array. `scripts/validate.py` loads the sibling definitions doc to check this (still fully offline — no network fetch, unlike `check_sync.py`).
- **A `params`/`mav_frames.rejects_unsupported` entry's `basis` is optional** — omitted means "same basis as this frame's own `basis`" (a documentation convention `scripts/validate.py` permits but doesn't compute; nothing resolves an "effective basis" anywhere). Only write a param-level `basis` when it genuinely differs from the frame's (e.g. the frame overall is `verified` by testing, but one param's earliest version is only known via `code-inspection` — see the mixed-confidence rule below for how to phrase that case instead, since a differing `basis` there usually belongs in `notes`, not as a per-param override). In practice this means most params should omit `basis` entirely; only messages/enums and each frame's own top-level `basis` are required.
- `earliest_checked_version` (**`"X.Y.Z"` only, never `"main"`** — same `releasedVersion` type as `last_checked_version`): scoped to a frame's own status only, not usable on messages/enums, not duplicated into params. Used when `added_version` is `true` to record "confirmed already present as of this checked release" as a floor/lower bound — distinct from `last_checked_version`, which records freshness of a negative fact. Dropped once a real `added_version` is discovered.
- **`accept_nan_or_int32max` / `nacks_on_non_sentinel_value`** (both booleans, both optional): sentinel-value facts about one param, living inline on that param's own `params.<index>_<name>` entry rather than in a separate structure. `accept_nan_or_int32max` — does this param's sentinel value (`NaN` for a float-encoded param, `INT32_MAX` for an int32-encoded one, e.g. mission-item lat/lon) get accepted — applies to *any* param, supported or not, since accepting the sentinel is a wire-level fact independent of whether the param's own function works. Where `supported` is the confirmed-implemented object form, it nests **inside** `supported` as a peer of `added_version` (there's a natural home for it there); where `supported` is a bare scalar (`false`/`null`/`"not-applicable"`, no object to nest into), it's a top-level sibling of `supported` instead — `scripts/validate.py` enforces whichever placement fits. `nacks_on_non_sentinel_value` — does the implementation correctly reject a real (non-sentinel) value on this param — only makes sense when the param is *not* the confirmed-implemented form (an implemented param is expected to accept real values, so the question is moot there), and always lives as a top-level sibling of `supported`; `scripts/validate.py` rejects it alongside a confirmed-implemented `supported`.
- **A command param named `"Empty"` is exempt from `supported`, not from these two sentinel keys.** `scripts/mavlink_xml.py`'s `_param_name()` synthesizes the literal name `"Empty"` for a reserved/undocumented upstream param slot (no `label` attribute in the XML). It still gets a normal `"<index>_Empty"` entry in the shared `definitions/<NAME>.json` roster (it's a real slot, just an undocumented one) — but "supported" isn't a meaningful question for it, so when it appears in a frame's `params` at all, `supported` is fixed to `"not-applicable"` and the entry exists purely to carry `accept_nan_or_int32max`/`nacks_on_non_sentinel_value` — a reserved slot's only legal value is the sentinel, so both facts are exactly the kind of thing worth recording for it. It's otherwise still optional (most frames simply won't mention it) — `scripts/validate.py` flags an `"Empty"` entry whose `supported` isn't `"not-applicable"`. Once upstream assigns `"Empty"` a real label — already caught by `check_sync.py`'s rename detection — it's an ordinary param from that point on, and the normal `params` gating rule applies immediately; `check_sync.py --fix` never stubs `params` compatibility in when appending a newly-added param still named `"Empty"`.
- `mav_frames`: coordinate-frame (`MAV_FRAME`) support — deliberately a separate key from the vehicle-type `frames` above (same English word, unrelated MAVLink concepts). `supported` is the list of accepted `MAV_FRAME_*` values (meaningful wherever the wire encoding carries a `frame` field — mission items via `MISSION_ITEM_INT`, and `COMMAND_INT`); `rejects_unsupported` is a support-statement answering "does it NACK an unsupported frame, and since when"; `default_frame_command_long` is a plain informational string (no pass/fail, no version) for when this command can also be sent as `COMMAND_LONG` (no frame field at all). Gated the same way as `params`.

**Terse notes, mixed-confidence statements, and the omit-vs-null convention apply equally to both shapes above.**

**Notes are extremely terse** — a fragment, ~10 words, no period. Say only what the structured fields can't. Cut restatements of `supported`/`basis`/`added_version`, hedges, and cross-refs the reader can already see. Name the exception, not the working case: "Alt not used (UseAltitude=false)", not "Only lat/lon-based gating (UseAltitude=false) is implemented — see param compatibility for details." `notes` may be a single string or an **array** of strings — use the array when there are two-plus genuinely distinct facts, one fragment each, rather than fusing them with "; ". Within a command frame, this cuts both ways: a param's `notes` is assumed to inherit the frame's own `notes` too, so drop a param-level note that just restates (or closely paraphrases) what the frame already says — write one only when it says something genuinely unique to that param.

**Don't assert internal implementation details (e.g. "stored"/"not stored") that the evidence can't actually show.** Protocol/behavioral testing (SITL, real hardware) only reveals observable outcomes — accepted/rejected, an effect present or absent — not internal mechanism, like whether a value is captured in a struct field versus discarded outright. A note claiming "never stored" or "stored but forced to NaN" overstates what black-box testing evidences; stick to what was actually observed (e.g. `supported: false` alone, or a note describing the observable behavior itself, like an exception the sentinel value triggers).

**Mixed-confidence statements: keep one `basis`, push the weaker fact into `notes`.** `basis` describes the whole support statement, not a specific field — so when, say, `added_version` is only known via a weaker method than the rest of the statement (e.g. `supported` is `testing`-confirmed on recent releases, but the *earliest* version is only known from `code-inspection` of old source), don't downgrade the shared `basis` to the weaker value — that understates the parts that really were tested. Set `basis` to the strongest method backing the statement's *current* facts, and add a terse `notes` fragment naming which specific field is weaker and why (see `MAV_CMD_CONDITION_GATE.json`'s px4 entry for a worked example: `basis: "testing"` with a note that `added_version` specifically is code-inspection-only, plus a note on the earliest version the project's test harness could actually exercise live).

**Omit vs. explicit `null` convention**: omit a field (`notes`, `impl_url`, `deprecated_version`, `removed_version`) when nobody has entered that information yet. Use explicit `null` only for a fact tooling actually determined — e.g. a param's `enumRef: null` means "confirmed: no enum reference," not "unknown."

**Don't record findings from an unreleased dev build as compatibility data.** `added_version: "main"` is legitimate for a durable code-presence fact (e.g. a merged PR or changelog entry: "landed on the dev branch, not yet released") — but don't set it, or write any `supported`/`notes` content, purely from testing a specific dev/pre-release snapshot (a particular git commit). "main" is a moving target with no stable identity across time — a test result from one day's build isn't a fact you can stand behind once the branch moves on, and there's no commit-pinned versioning scheme (e.g. `main+<hash>`) to make it comparable later. Same rule `last_checked_version`/`earliest_checked_version` already follow — wait for an actual tagged release before recording it.

**Enums referenced by a field/param are not duplicated.** A message field or command param that takes its values from an enum carries `enumRef: "<ENUM_NAME>"` (or `null`). The enum's own value-level compatibility lives once in its own `enums/<ENUM_NAME>.json`.

## ArduPilot versioning

ArduPilot ships separate firmware per vehicle (Copter/Plane/Rover/Sub/Tracker/Blimp) from one monorepo; stable releases land as a roughly coordinated `X.Y` wave but patch-level (`X.Y.Z`) cadence differs per vehicle, and not every vehicle gets every point release. For messages/enums, `ardupilot.default` is therefore a **major.minor-level approximation of the shared implementation** (most MAVLink handling lives in `libraries/GCS_MAVLink`), not a claim about one vehicle's exact build — add a variant override only for a real functional difference (unsupported on that vehicle, a different minor version, partial support), not to chase per-vehicle patch numbers. Commands have no such approximation: since `frames` has no `default` fallback, every command fact is scoped to the specific frame(s) actually evaluated.

ArduPilot's own vehicle-type vocabulary also splits conventional fixed-wing (`plane`) from VTOL/quadplane (`standard_quadplane`) as two separate `frames` entries, even though both run "Plane" firmware — the flight behavior genuinely differs (e.g. takeoff), so they're tracked independently rather than one inheriting from the other.

## `schema/versions.json`

Flat list of released `X.Y.Z` numbers per stack (ArduPilot: whole-project numbers, not per-vehicle tags like `Copter-4.5.0`). Maintained by hand — append new releases as they ship. Any concrete version string used in `supported.added_version`/`deprecated_version`/`removed_version`/`last_checked_version` must appear here for its stack.

## Sync drift categories (`scripts/check_sync.py`)

- **New entity upstream** — no stub file yet. Not fixed by this script; run `generate_stubs.py`.
- **Addition/rename within an existing entity**:
  - Messages/enums — a `name`/`enumRef` differs from upstream, or upstream declares a field/value not yet in the doc. **Auto-fixable** with `--fix`: patches the identity field or appends the new sub-entity, with fresh `unknown` compatibility for whichever stacks the parent already confirms implemented (per the sub-entity gating rule — omitted otherwise); existing `compatibility` data is never touched.
  - Commands — a param's `name`/`enumRef` differs from upstream, or upstream declares a param not yet in the doc, in the shared `definitions/<NAME>.json` roster. **Auto-fixable** with `--fix`, coordinated across all three files for that command: the identity change is patched (or the new key added) in `definitions/<NAME>.json` itself, and the same `"<index>_<name>"` key is rewritten (rename) or gated-stubbed with fresh `unknown` compatibility (addition — for every `(stack, frame)` already confirmed implemented) wherever it's referenced in **both** the `mission/<NAME>.json` and `command/<NAME>.json` compatibility docs' `frames.<frame>.params` maps. `"Empty"` → a real name is identity-only in `definitions/<NAME>.json`, same as before — never proactively stubs compatibility into already-confirmed frames.
- **Removed upstream** — a field/param/value/entity no longer exists upstream. **Never auto-fixed or deleted** — flagged for manual review, since deleting would destroy compat history.

## Keeping `README.md` in sync

`README.md` duplicates parts of this file for a public-facing audience: the layout tree, the entity doc shape example, the `compatibility` field reference, the scripts list, and the validation checklist. Any change here that changes what one of those says — a new/changed `compatibility` field, a new gating/validation rule, a script's behavior, a new sync-drift category — **must** update the matching part of `README.md` in the same change, not as a follow-up. Check it before calling a schema/script/convention change done, whether or not the user asked for README specifically.

## Working with stub data

- Don't hand-edit the structure `generate_stubs.py`/`check_sync.py` produce (field order, key names, nesting) — only fill in `compatibility`/`notes`/`impl_url` values. `data/dialects/common/mav_cmd/definitions/*.json` files take **no** human-entered content at all, ever (not even `notes`) — they're purely mechanical, regenerated/patched by the scripts as upstream MAVLink XML changes.
- After editing: `python scripts/validate.py` must pass before opening a PR.
- `python scripts/generate_stubs.py` / `check_sync.py [--fix]` both accept `--cache-dir DIR` (a directory of pre-fetched `<dialect>.xml` files) to avoid network fetches, e.g. for local testing.
