# CLAUDE.md

## Layout

- `data/dialects/<minimal|common|standard>/messages/<NAME>.json` — one MAVLink message per file.
- `data/dialects/<minimal|common|standard>/enums/<NAME>.json` — one enum per file. `MAV_CMD` is **not** here — see commands below.
- `data/dialects/common/mav_cmd/definitions/<MAV_CMD_NAME>.json` — one shared, purely upstream-derived identity file per `MAV_CMD_*` entry: `value` and the `params` roster (index/name/enumRef). Holds no compatibility data and no human-entered content at all — see below.
- `data/dialects/common/mav_cmd/mission/<MAV_CMD_NAME>.json` and `.../command/<MAV_CMD_NAME>.json` — each `MAV_CMD_*` entry additionally gets up to two independent compatibility-only files (only for the contexts upstream marks `mission="true"`/`command="true"` on the `<entry>`), one for mission-item usage, one for direct-command usage, both referencing the one shared `definitions/<NAME>.json` for identity. `mav_cmd/` only exists under `common` (MAV_CMD is defined there).
- `schema/*.schema.json` — JSON Schema per doc type; `compatibility-entry.schema.json` holds the shared statement/`compatibility` definitions, `$ref`'d by the others; `mav_cmd_definition.schema.json` is the schema for the `definitions/` files above.
- `schema/vocab.json` — controlled vocab: `stacks`, `frames` (per stack), `basis`, `dialects`.
- `schema/versions.json` — known released version numbers per stack, used to validate concrete version strings.
- `scripts/mavlink_xml.py` — shared upstream MAVLink XML parser (used by both scripts below).
- `scripts/generate_stubs.py` — creates missing stub files for new upstream entities. Idempotent, additive only.
- `scripts/check_sync.py` — diffs `data/` against fresh upstream XML; `--fix` patches renames/additions in place.
- `scripts/validate.py` — CI validation entrypoint (see README for the checklist).

## Support statements (every doc type)

Every compatibility fact — a message/enum (or one of its fields/values) on a stack variant, a command's vehicle frame, a param within it, a sentinel subfeature, `mav_frames.rejects_unsupported` — is one **statement** (or a history array of them). Modeled on mdn/browser-compat-data's `simple_support_statement`:

```json
{ "version_added": "≤1.17.0", "version_deprecated"?: "...", "version_removed"?: "...",
  "basis": "testing", "last_checked_version"?: "1.17.0", "notes"?: ..., "impl_url"?: "..." }
```

- `version_added` (required) — one field carries both "is it supported" and "since when":
  - `"X.Y.Z"` — the **known first** version.
  - `"≤X.Y.Z"` — confirmed present as of that release, earlier releases not checked (a lower bound). Use this instead of guessing a first version. The `≤` (U+2264) is literal in the JSON, never `≤`-escaped; the version after it must be in `schema/versions.json`, and it's never combined with `"main"`.
  - `"main"` — dev branch only (a durable code-presence fact such as a merged PR — see the dev-build rule below).
  - `false` — confirmed not supported.
  - `null` — not yet evaluated (stubs, or a frame that couldn't be evaluated and has a note saying why).
  - `"not-applicable"` — **commands only**: this `MAV_CMD` doesn't apply for that frame in this wire context, or a param doesn't apply within an otherwise-supported frame. `scripts/validate.py` rejects it on messages/enums.
- `version_deprecated` / `version_removed` (optional, **omitted** when not applicable): `"main"` | `"X.Y.Z"`. Only valid when `version_added` is a real version; must not precede it (`version_removed` strictly after).
- `basis`: `unknown` | `code-inspection` | `testing` | `verified` — how the statement was established. Required on messages/enums and on each command frame's own `supported`; optional everywhere beneath a frame (params, sentinel subfeatures, `rejects_unsupported`), where omitted means "same as the frame's own `basis`" (a documentation convention `scripts/validate.py` permits but doesn't compute). In practice most params omit it.
- `last_checked_version`: **`"X.Y.Z"` only — never `"main"`** (`schema/compatibility-entry.schema.json`'s `releasedVersion` def; enforced by both the schema and `check_version_field(..., allow_main=False)`). The release this statement was last confirmed against — a fixed baseline comparable against `schema/versions.json` to decide "has a newer release shipped since, and does it need re-verification?". Recommended on every `version_added: false` statement backed by real evaluation (implemented-ness can change in a later release); also valid on an open-ended supported statement ("still present as of"). Not valid alongside `version_removed` (a closed range doesn't go stale). Don't set it from a dev/pre-release build — wait for a tagged release.
- `notes`, `impl_url`: optional, omitted when unset. `impl_url` is a single URL (tracking issue, PR, or doc).

**History arrays.** Any statement may instead be a non-empty array of statements to record an added → removed → re-added timeline. Ordered **newest first** (mdn/browser-compat-data convention — `[0]` is the current state); every entry needs a real `version_added` (no `false`/`null`/`"not-applicable"` entries — "not supported before X" is already implied by `version_added: "X"`, and "not supported after a removal" by the older entry's `version_removed`); ranges must not overlap, so every entry but the first needs a `version_removed` no later than the next-newer entry's `version_added`. Each entry carries its own `basis`/`notes`, so a range known only by code-inspection and a later range confirmed by testing can each say so. `scripts/validate.py` enforces ordering and overlap.

**"Implemented" (for gating) means any entry has a real `version_added`** (`"X.Y.Z"`, `"≤X.Y.Z"` or `"main"`) — including a range that was later removed, since facts about that range are still meaningful.

## `compatibility` shape: messages and enums

```json
"compatibility": {
  "<stack>": {
    "default": <statement or history>,
    "<variant>": <statement or history>
  }
}
```

- `<stack>`: `px4` | `ardupilot` (from `vocab.json`).
- `<variant>`: `"default"` (stack-wide, propagates to every variant with no key of its own) or a frame name from `vocab.json`'s `frames` list for that stack. A variant key **overrides** `default` for that variant only — no explicit "propagates" marker needed.

**Sub-entity (field/value) compatibility: absent means unknown, and it's gated by its parent.** Same rule as command params: only the fields/values actually evaluated carry a `compatibility` entry for a given `(stack, variant)` — there's no requirement to list every field once the parent is known, and no placeholder `version_added: null` entries. A field/value may write a `(stack, variant)` key **only if** the parent message/enum's effective statement for that variant is implemented (see above), resolving the parent's `default`→variant inheritance; when the parent there is `false`/`null`, omit it — there's nothing to evaluate until the parent is known. A field/value's own inherited `default` simply doesn't apply to a variant where the parent overrides to not-implemented (e.g. parent `default` implemented, `copter: false` — a field's `default` is fine and says nothing about `copter`). `compatibility` itself is optional on a sub-entity. `scripts/validate.py` flags only present-when-not-allowed. Neither `generate_stubs.py` nor `check_sync.py --fix` ever writes sub-entity compatibility.

## `compatibility` shape: commands (`MAV_CMD`)

Commands use a per-vehicle-frame shape instead of `default`+variant-override — testing happens incrementally per vehicle (multicopter first, then fixed-wing, etc.), frames can support genuinely different combinations of params, and there's no assumed inheritance between frames.

```json
"compatibility": {
  "<stack>": { "frames": {} }                  // untested -- nothing evaluated for any frame yet
}
```
```json
"compatibility": {
  "<stack>": {
    "frames": false,                           // confirmed unsupported on every frame (blanket fact)
    "basis": "...", "notes"?: ..., "impl_url"?: ..., "last_checked_version"?: "..."
  }
}
```
```json
"compatibility": {
  "<stack>": {
    "frames": {
      "<frame>": {
        "supported": <statement or history>,
        "accept_nan_or_int32max"?: <statement or history>,
        "nacks_on_non_sentinel_value"?: <statement or history>,
        "params"?: {
          "<index>_<name>": {
            "supported"?: <statement or history>,
            "accept_nan_or_int32max"?: <statement or history>,
            "nacks_on_non_sentinel_value"?: <statement or history>
          }
        },
        "mav_frames"?: { "supported"?: ["MAV_FRAME_..."], "rejects_unsupported"?: <statement or history>, "default_frame_command_long"?: "MAV_FRAME_..." }
      }
    }
  }
}
```

- `<frame>`: a name from `vocab.json`'s `frames` list for that stack (e.g. `plane`, `standard_quadplane`, `copter` for ArduPilot; `multicopter`, `fixedwing`, `vtol` for PX4). **No `default` fallback within `frames`** — every known/tested frame is written out in full, even when two frames share an identical result; absence of a frame key always means untested/unknown, nothing is ever inherited between frames.
- `basis`/`notes`/`impl_url`/`last_checked_version` are only valid as siblings of `frames` when `frames` is `false` (describing that blanket claim); once `frames` is an object, each frame's statements carry their own.
- **`params`: only the params actually evaluated appear; an absent param is unknown** — same rule as absent frames. There's no "every param must be listed" requirement and no "unlisted params are false" shortcut. `params` (and the frame-level sentinel subfeatures and `mav_frames`) may exist only once the frame is implemented (see "Implemented" above). A param's supported ranges must sit inside the frame's own supported ranges — except that a frame range starting at a `≤` lower bound doesn't constrain how early a param's range may start (earlier releases simply weren't checked). Param identity (`{"<index>_<name>": {"enumRef": ...}}`) lives once in the sibling `mav_cmd_definition.schema.json` doc (`data/dialects/common/mav_cmd/definitions/<NAME>.json`), not in the compatibility doc itself — it's purely upstream-derived and identical between a command's `mission` and `command` context files. `scripts/validate.py` loads that sibling doc to check `params` keys (still fully offline — no network fetch, unlike `check_sync.py`).
- **Sentinel subfeatures** — `accept_nan_or_int32max` (is this param's sentinel value — `NaN` for a float-encoded param, `INT32_MAX` for an int32-encoded one, e.g. mission-item lat/lon — accepted) and `nacks_on_non_sentinel_value` (is a real, non-sentinel value correctly rejected while the param isn't supported). Each is its own statement/history, so it has its own version range: e.g. a param that NACKed real values up to 1.17.0 and became supported in 1.18.0 is `"supported": {"version_added": "1.18.0"}, "nacks_on_non_sentinel_value": {"version_added": "≤1.17.0", "version_removed": "1.18.0"}`. "Doesn't NACK" is `{"version_added": false, "last_checked_version": "..."}`.
  - **Frame-level default, param-level override.** Set on the frame, a sentinel subfeature is the default for every param without its own; a param's own entry overrides it. `nacks_on_non_sentinel_value` from the frame only applies to a param while that param isn't supported. Only write a frame-level value when every param it would apply to was actually evaluated (list the exceptions on the params) — otherwise it claims results for untested params; record per-param instead.
  - A param's own `nacks_on_non_sentinel_value` ranges must not overlap its `supported` ranges — `scripts/validate.py` enforces this, which is what makes the `version_removed` in the example above required rather than implied.
- **A command param named `"Empty"`** — `scripts/mavlink_xml.py`'s `_param_name()` synthesizes the literal name `"Empty"` for a reserved/undocumented upstream param slot (no `label` attribute in the XML). It still gets a normal `"<index>_Empty"` entry in the shared `definitions/<NAME>.json` roster, but "supported" isn't a meaningful question for it: in a frame's `params` its `supported` is either omitted or `{"version_added": "not-applicable"}` (optional — useful when writing out a frame's full param list), and its entry otherwise exists only to carry sentinel-subfeature overrides. `scripts/validate.py` flags any other `supported` on an `"Empty"` param. Once upstream assigns `"Empty"` a real label — already caught by `check_sync.py`'s rename detection — it's an ordinary param from that point on.
- `mav_frames`: coordinate-frame (`MAV_FRAME`) support — deliberately a separate key from the vehicle-type `frames` above (same English word, unrelated MAVLink concepts). `supported` is the list of accepted `MAV_FRAME_*` values (meaningful wherever the wire encoding carries a `frame` field — mission items via `MISSION_ITEM_INT`, and `COMMAND_INT`); `rejects_unsupported` is a statement answering "does it NACK an unsupported frame, and since when"; `default_frame_command_long` is a plain informational string (no pass/fail, no version) for when this command can also be sent as `COMMAND_LONG` (no frame field at all).

**Terse notes, mixed-confidence statements, and the omit-vs-null convention apply equally to both shapes above.**

**Notes are extremely terse** — a fragment, ~10 words, no period. Say only what the structured fields can't. Cut restatements of `version_added`/`basis`, hedges, and cross-refs the reader can already see. Name the exception, not the working case: "Alt not used (UseAltitude=false)", not "Only lat/lon-based gating (UseAltitude=false) is implemented — see param compatibility for details." `notes` may be a single string or an **array** of strings — use the array when there are two-plus genuinely distinct facts, one fragment each, rather than fusing them with "; ". Within a command frame, this cuts both ways: a param's `notes` is assumed to inherit the frame's own `notes` too, so drop a param-level note that just restates (or closely paraphrases) what the frame already says — write one only when it says something genuinely unique to that param.

**Don't assert internal implementation details (e.g. "stored"/"not stored") that the evidence can't actually show.** Protocol/behavioral testing (SITL, real hardware) only reveals observable outcomes — accepted/rejected, an effect present or absent — not internal mechanism, like whether a value is captured in a struct field versus discarded outright. A note claiming "never stored" or "stored but forced to NaN" overstates what black-box testing evidences; stick to what was actually observed (e.g. `version_added: false` alone, or a note describing the observable behavior itself, like an exception the sentinel value triggers).

**Mixed-confidence statements: keep one `basis`, push the weaker fact into `notes`.** `basis` describes the whole statement, not a specific field — so when, say, `version_added` is only known via a weaker method than the rest of the statement (e.g. support is `testing`-confirmed on recent releases, but the *earliest* version is only known from `code-inspection` of old source), don't downgrade the shared `basis` to the weaker value — that understates the parts that really were tested. Set `basis` to the strongest method backing the statement's *current* facts, and add a terse `notes` fragment naming which specific field is weaker and why (see `MAV_CMD_CONDITION_GATE.json`'s px4 entry for a worked example: `basis: "testing"` with a note that `version_added` specifically is code-inspection-only, plus a note on the earliest version the project's test harness could actually exercise live). If you don't trust the weaker first version at all, use a `≤` lower bound at the earliest release you actually tested instead. Split into separate history entries only for genuinely separate ranges (removed and re-added), not to attach two bases to one range.

**Omit vs. explicit `null` convention**: omit a field (`notes`, `impl_url`, `version_deprecated`, `version_removed`) when nobody has entered that information yet. Use explicit `null` only where it's a deliberate value — `version_added: null` ("not yet evaluated", on stubs), or a param's `enumRef: null` ("confirmed: no enum reference", not "unknown").

**Don't record findings from an unreleased dev build as compatibility data.** `version_added: "main"` is legitimate for a durable code-presence fact (e.g. a merged PR or changelog entry: "landed on the dev branch, not yet released") — but don't set it, or write any statement/`notes` content, purely from testing a specific dev/pre-release snapshot (a particular git commit). "main" is a moving target with no stable identity across time — a test result from one day's build isn't a fact you can stand behind once the branch moves on, and there's no commit-pinned versioning scheme (e.g. `main+<hash>`) to make it comparable later. Same rule `last_checked_version` and `≤` lower bounds already follow — wait for an actual tagged release before recording it.

**Enums referenced by a field/param are not duplicated.** A message field or command param that takes its values from an enum carries `enumRef: "<ENUM_NAME>"` (or `null`). The enum's own value-level compatibility lives once in its own `enums/<ENUM_NAME>.json`.

## ArduPilot versioning

ArduPilot ships separate firmware per vehicle (Copter/Plane/Rover/Sub/Tracker/Blimp) from one monorepo; stable releases land as a roughly coordinated `X.Y` wave but patch-level (`X.Y.Z`) cadence differs per vehicle, and not every vehicle gets every point release. For messages/enums, `ardupilot.default` is therefore a **major.minor-level approximation of the shared implementation** (most MAVLink handling lives in `libraries/GCS_MAVLink`), not a claim about one vehicle's exact build — add a variant override only for a real functional difference (unsupported on that vehicle, a different minor version, partial support), not to chase per-vehicle patch numbers. Commands have no such approximation: since `frames` has no `default` fallback, every command fact is scoped to the specific frame(s) actually evaluated.

ArduPilot's own vehicle-type vocabulary also splits conventional fixed-wing (`plane`) from VTOL/quadplane (`standard_quadplane`) as two separate `frames` entries, even though both run "Plane" firmware — the flight behavior genuinely differs (e.g. takeoff), so they're tracked independently rather than one inheriting from the other.

## `schema/versions.json`

Flat list of released `X.Y.Z` numbers per stack (ArduPilot: whole-project numbers, not per-vehicle tags like `Copter-4.5.0`). Maintained by hand — append new releases as they ship. Any concrete version string used in `version_added` (with any `≤` prefix stripped)/`version_deprecated`/`version_removed`/`last_checked_version` must appear here for its stack.

## Sync drift categories (`scripts/check_sync.py`)

- **New entity upstream** — no stub file yet. Not fixed by this script; run `generate_stubs.py`.
- **Addition/rename within an existing entity**:
  - Messages/enums — a `name`/`enumRef` differs from upstream, or upstream declares a field/value not yet in the doc. **Auto-fixable** with `--fix`: patches the identity field or appends the new sub-entity with no `compatibility` (absent already means unknown); existing `compatibility` data is never touched.
  - Commands — a param's `name`/`enumRef` differs from upstream, or upstream declares a param not yet in the doc, in the shared `definitions/<NAME>.json` roster. **Auto-fixable** with `--fix`: the identity change is patched (or the new key added) in `definitions/<NAME>.json` itself; a rename also rewrites the same `"<index>_<name>"` key (keeping its data) wherever it's referenced in **both** the `mission/<NAME>.json` and `command/<NAME>.json` docs' `frames.<frame>.params` maps. An addition touches only `definitions/<NAME>.json` — an absent param in a frame's `params` already means unknown, so nothing is stubbed.
- **Context not allowed upstream** — a `mission/<NAME>.json` or `command/<NAME>.json` exists, but upstream's `<entry>` lacks `mission="true"` / `command="true"` respectively. **Auto-fixable** with `--fix`: that context file is deleted (`definitions/<NAME>.json` stays). `generate_stubs.py` likewise only creates a context file for contexts upstream allows.
- **Removed upstream** — a field/param/value/entity no longer exists upstream. **Never auto-fixed or deleted** — flagged for manual review, since deleting would destroy compat history.

## Keeping `README.md` in sync

`README.md` duplicates parts of this file for a public-facing audience: the layout tree, the entity doc shape example, the `compatibility` field reference, the scripts list, and the validation checklist. Any change here that changes what one of those says — a new/changed `compatibility` field, a new gating/validation rule, a script's behavior, a new sync-drift category — **must** update the matching part of `README.md` in the same change, not as a follow-up. Check it before calling a schema/script/convention change done, whether or not the user asked for README specifically.

## Working with stub data

- Don't hand-edit the structure `generate_stubs.py`/`check_sync.py` produce (field order, key names, nesting) — only fill in `compatibility` statements (including their `notes`/`impl_url`) and entity-level `notes`. `data/dialects/common/mav_cmd/definitions/*.json` files take **no** human-entered content at all, ever (not even `notes`) — they're purely mechanical, regenerated/patched by the scripts as upstream MAVLink XML changes.
- After editing: `python scripts/validate.py` must pass before opening a PR.
- Data files are UTF-8 without a byte-order mark (RFC 8259; `scripts/validate.py` reports anything else). Write `≤` literally — the scripts save with `ensure_ascii=False` so it round-trips as-is.
- `python scripts/generate_stubs.py` / `check_sync.py [--fix]` both accept `--cache-dir DIR` (a directory of pre-fetched `<dialect>.xml` files) to avoid network fetches, e.g. for local testing.
