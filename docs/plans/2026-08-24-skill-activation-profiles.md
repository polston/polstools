# Skill activation profiles — implementation plan

Date: 2026-08-24. Status: implemented on the feature branch; final release
validation and publication remain outside this plan record.

The `p` plugin should let an installation keep home-only workflows out of
ordinary work sessions without forking the plugin, editing installed cache
files, or relying on a model to remember a prose exception. Existing installs
must retain their current behavior until the operator chooses another profile.

## Outcome

Add zero-argument `$p:home` and `$p:work` skills, one cross-harness activation
controller, one versioned policy manifest, and a small gate at the start of
every invocable plugin skill. The two commands switch the current session, in
the same way `$p:fmt-on` and `$p:fmt-off` do. The shipped profiles are:

- `home` — the compatibility default; every existing skill remains
  enabled.
- `work` — ordinary software-development skills remain enabled, while
  repository-publication audits and local session-history analysis are
  disabled. Mixed-purpose skills remain usable, but their history-reading
  branch is disabled.

The profile is an invocation policy, not an operating-system security
boundary. A disabled skill must stop before running commands or reading data,
but the same model may still possess general shell and file tools. Explicit
higher-priority repository instructions also remain authoritative; the
controller must report that conflict rather than claim the policy overrode it.

## Native capability findings

The installed CLIs and current vendor documentation were checked before
choosing the design.

### Codex

- Codex CLI 0.149.0 and the current configuration schema expose
  `skills.config` entries with `enabled` plus a skill name or exact `SKILL.md`
  path. A local prompt-debugger probe confirmed that an exact installed plugin
  `SKILL.md` path removes that skill from a fresh Codex catalog. Name selectors
  did not remove the namespaced plugin skill in the same probe.
- The working path selector is tied to Codex's versioned plugin cache. It
  therefore becomes stale after a plugin update and cannot be the portable
  source of truth. Expose it only as an explicit future-session visibility
  adapter, while retaining the version-independent runtime gate.
- A skill can ship `agents/openai.yaml` with
  `policy.allow_implicit_invocation: false`. That is static source metadata:
  the skill remains explicitly invocable, and the value cannot vary between a
  home and work installation without publishing or materializing different
  plugin contents.
- The hosted API Skills lifecycle is a different facility from the local Codex
  plugin skill catalog and does not solve this problem.

### Claude Code

- Claude Code 2.1.241 enables or disables a whole plugin. It does not expose a
  plugin subcommand for one skill.
- `disable-model-invocation: true` is the static source equivalent of Codex's
  explicit-only policy.
- Claude's `skillOverrides` setting can make ordinary skills `on`,
  `name-only`, `user-invocable-only`, or `off`, but the official documentation
  explicitly excludes plugin skills. Plugin skills are managed at plugin
  granularity.
- `claude plugin update` reports that a restart is required to apply an update.
  A profile change implemented by this plan can take effect during a session;
  loading new plugin code still follows the harness lifecycle.

### Status lines

- Claude's status-line command receives the current `session_id` and can print
  arbitrary text. Both the bundled renderer and a ccstatusline custom-command
  widget can therefore show `p:h` or `p:w` from the same session state.
- Codex's supported `tui.status_line` is an ordered list of native item
  identifiers. The current identifier set has no custom-text or active skill
  profile item. Do not replace the native footer, hijack the thread title, or
  write an unsupported identifier merely to imitate Claude's indicator.

Sources: [Claude Code skills](https://code.claude.com/docs/en/slash-commands),
[Claude Code plugins](https://code.claude.com/docs/en/plugins),
[Claude Code status lines](https://code.claude.com/docs/en/statusline), and the
[Codex configuration reference](https://developers.openai.com/codex/config-file/config-reference).

## Chosen architecture

Keep one `p` plugin and define a stable policy seam inside it:

```text
tracked plugin policy
        |
        v
profile + component inventory ---- local selection (environment/session/global)
        |                                      |
        +------------------+-------------------+
                           v
                 skill-profile-ctl check
                           |
                   enabled / disabled / error
                           |
                +----------+----------+
                v                     v
          Claude p:h/p:w       Codex native adapter
```

The manifest and runtime gate remain canonical. Codex's exact-path skill
configuration is an adapter for new-session catalog visibility; it is not
allowed to replace the gate because cache paths change between versions.

Do not add a SessionStart or UserPromptSubmit hook. A profile-controller bug
must be able to stop only a governed skill, never prompt submission, session
startup, unrelated tools, or the response-format feature.

### Files to add

- `plugins/p/profiles/skill-activation-v1.json` — canonical component,
  capability, and profile definitions.
- `plugins/p/bin/skill-profile-ctl` — stdlib-only Python 3 controller.
- `plugins/p/lib/skill_activation.py` — shared policy loader and resolver used
  by the controller and the installed status-label helper, so precedence and
  failure behavior are implemented once.
- `plugins/p/renderer/skill-profile-label.py` — fail-soft entry point copied
  with the resolver and policy into the stable statusline state directory.
- `plugins/p/skills/home/` and `plugins/p/skills/work/` — native cross-harness
  toggle skills with relative `scripts/toggle.py` launchers matching the
  `fmt-on`/`fmt-off` pattern.
- `plugins/p/skills/managing-skill-activation/SKILL.md` — cross-harness natural
  language entry point for status, global defaults, and individual overrides.
- `plugins/p/tests/test_skill_activation.py` — schema, precedence, gate,
  coverage, failure, and state-lifecycle tests.

### Files to change

- Every `plugins/p/skills/*/SKILL.md` and `plugins/p/commands/*.md` gets one
  short, mandatory first-step gate using its stable component ID. Commands are
  included because Codex materializes them as skills and Claude exposes them in
  the same invocable surface.
- The format-maintenance skill gets an operation-level capability check before
  its history-reading branch. Its mechanical repair path stays available in
  `work`.
- The tool-scouting skill gets the same operation-level check before it falls
  back to generating a retrospective pack. Scouting from an already supplied
  friction remains available.
- `plugins/p/bin/statusline-ctl`, the bundled Claude renderer, and statusline
  tests gain an owned `profile-sync` operation. A recognized ccstatusline
  configuration receives one tagged custom-command widget while all existing
  lines, widgets, colors, and commands are preserved.
- An explicit optional Codex catalog sync transactionally updates only owned
  `skills.config` entries for the currently installed plugin paths. Profile
  changes never rewrite Codex configuration automatically, because a running
  session cannot reload a skill removed from its startup catalog.
- Both plugin manifest/catalog descriptions and versions move together in the
  release change. No unsupported activation fields are added to a plugin
  manifest.

No existing hook wiring changes.

## Canonical policy schema

Use structured JSON rather than encoding policy in descriptions or directory
names. The first schema is deliberately small:

```json
{
  "schema_version": 1,
  "default_profile": "home",
  "capabilities": {
    "control-plane": {},
    "core": {},
    "local-session-history": {},
    "repository-publication-data": {}
  },
  "profiles": {
    "home": {
      "capabilities": "all"
    },
    "work": {
      "capabilities": ["control-plane", "core"]
    }
  },
  "components": {
    "home": {
      "source": "skill",
      "requires": ["control-plane"]
    },
    "work": {
      "source": "skill",
      "requires": ["control-plane"]
    },
    "managing-skill-activation": {
      "source": "skill",
      "requires": ["control-plane"]
    },
    "auditing-a-repo-for-private-data": {
      "source": "skill",
      "requires": ["repository-publication-data"]
    }
  }
}
```

The real manifest lists every source skill and every command-derived skill.
Each component has at least one required capability. A mixed-purpose component
may also list `conditional_capabilities`; its baseline can run when a
conditional capability is disabled, but it must gate that specific branch.

The control plane is always enabled and cannot be disabled by an override. A
schema validator rejects:

- a skill or command on disk that is absent from `components`;
- a manifest component with no corresponding source;
- an unknown profile, component, or capability;
- a component with no required capability;
- a profile that omits `control-plane`;
- duplicate or malformed component identifiers.

This coverage rule matters more than a permissive default. A new skill cannot
silently land in the work profile without an explicit classification.

## Initial classification

`work` disables these whole skills because their normal procedure necessarily
reads the governed data:

- `auditing-a-repo-for-private-data` — `repository-publication-data`;
- `auditing-workflow-rules-against-behavior` — `local-session-history`;
- `counting-stopped-promises` — `local-session-history`;
- `deciding-the-prompt-cache-ttl` — `local-session-history`;
- `finding-friction-in-recent-sessions` — `local-session-history`.
- `reviewing-evaluation-taxonomies` — `local-session-history` because its
  normal procedure discovers and reviews private local evaluation evidence.

Two components are limited rather than wholly disabled:

- `maintaining-the-format-plugin` remains `core`; its behavioral history audit
  requires `local-session-history`.
- `scouting-tools-for-open-frictions` remains `core` when given a named
  friction; deriving one from transcripts requires `local-session-history`.

All remaining current skills and command-derived skills are `core`. The new
`home`, `work`, and management skills are `control-plane`.

## Local selection and overrides

The repository carries profile definitions; it must not carry the operator's
selected context. Canonical profile state contains only generic profile names,
component IDs, and booleans—never repository paths, employer names, account
names, or session content. Harness-native adapter configuration may contain the
installed paths that its schema requires, but those paths are neither canonical
state nor tracked or printed by this plugin.

One complete policy document has this shape:

```json
{
  "schema_version": 1,
  "profile": "work",
  "overrides": {
    "checking-branch-base-before-a-pr": false
  }
}
```

Precedence is replacement, not merging:

1. A valid `P_SKILL_PROFILE` environment value selects a locked profile and
   ignores session/global overrides. This is the zero-maintenance choice for a
   dedicated work shell or launcher.
2. A valid per-session document replaces global state for that session.
3. A valid user-global document applies across both harnesses.
4. With no state, the tracked `default_profile` applies (`home`).

Within a session or global document, an explicit component override wins over
the selected profile. Overrides cannot change `control-plane`.

Use the platform's user configuration directory for global state and the
operating-system temporary directory for session state. Support task-specific
test/portability overrides such as `P_SKILL_CONFIG_FILE` and
`P_SKILL_STATE_DIR`; do not reuse common environment variables. State writes
are atomic replace operations with restrictive permissions where the platform
supports them.

An invalid higher-precedence source is not skipped. Publication/history checks
and conditional history branches fail closed. Components requiring only `core`
or `control-plane` remain available with a warning, so a broken policy cannot
disable unrelated work or its own repair commands. `status` identifies the
source and schema error without printing its absolute path or contents.
Explicit `home`, `work`, and reset operations may replace or remove malformed
state; silently treating it as `home` for sensitive work is forbidden.

## Command UX

The primary interface is deliberately as small as the format toggle:

- `$p:home` selects `home` for the current session and confirms `p:h`.
- `$p:work` selects `work` for the current session and confirms `p:w`.

Both are zero-argument, session-scoped commands. They do not rewrite user
configuration and they affect the next skill invocation immediately. The
management skill remains the less-common interface for changing the default or
overriding one component.

If `P_SKILL_PROFILE` locks the process to a different profile, the toggle must
refuse without writing ineffective session state and name the active lock. It
must never confirm a label that is not the effective profile.

The controller is also directly scriptable:

```text
skill-profile-ctl status [--json]
skill-profile-ctl profiles
skill-profile-ctl home
skill-profile-ctl work
skill-profile-ctl use home|work --global
skill-profile-ctl enable COMPONENT --session|--global
skill-profile-ctl disable COMPONENT --session|--global
skill-profile-ctl reset --session|--global
skill-profile-ctl label [--session-id ID]
skill-profile-ctl sync-native
skill-profile-ctl check COMPONENT
skill-profile-ctl check-capability CAPABILITY [--component COMPONENT]
skill-profile-ctl validate
```

`home` and `work` are the only implicit-scope mutations: they always mean the
current session. Every other mutating command requires an explicit scope. The
management skill must not guess `--global`. Typical requests are:

- `$p:home`
- `$p:work`
- `$p:managing-skill-activation make the work profile global`
- `$p:managing-skill-activation turn off the repository privacy audit`
- `$p:managing-skill-activation show what is disabled and why`

`status` prints the effective source, profile, explicit overrides, and three
component buckets: enabled, disabled, and limited. It prints identifiers, not
local paths.

Changing the current-session policy takes effect on the next invocation because
every gate reads current state. A global change applies immediately to sessions
without a higher-priority environment or session selection and to future
sessions. Neither change requires a plugin reinstall or session restart. The
disabled skill may remain visible in the harness catalog; visibility is a
separate loader concern.

## Status indicator

Use `p:h` and `p:w`; do not use `p:~`, because `~` already means a shortened
home directory beside the current-working-directory segment. The label is
presentation only and never participates in policy resolution.

### Claude bundled renderer

Claude supplies `session_id` to the renderer. Resolve the effective profile
with the same precedence as the controller and append a dim `p:h` or `p:w` to
the first line. With no selected state, show the default `p:h`; an invalid
higher-precedence source renders `p:?`. Neither case may blank the rest of the
status line or return a failure.

### Claude with ccstatusline

Preserve ccstatusline and its existing layout. Extend `statusline-ctl` with an
explicit, transactional `profile-sync` operation that:

1. installs a stable, versioned status bundle under the statusline state
   directory, containing the shared resolver and policy needed by the tiny
   label helper;
2. adds one custom-command widget tagged as owned by `p` to the first non-empty
   ccstatusline row;
3. leaves every unrelated row, widget, command, color, separator, timer, and
   setting unchanged; and
4. restores or updates only the tagged widget.

The custom command receives Claude's status JSON on stdin and prints only the
short label. The bundled renderer calls the same stable helper. `$p:home` and
`$p:work` refresh this owned bundle and integration after the profile switch,
so a newly loaded plugin version replaces the prior copied resolver without
depending on a versioned cache path. If the status integration cannot be read
or updated, keep the successful profile change, preserve the existing
statusline, and report the indicator failure separately.

### Codex native footer

Keep the existing aligned native footer unchanged. Current Codex configuration
accepts native status item identifiers, not arbitrary text, so `p:h`/`p:w`
cannot be added honestly. `$p:home` and `$p:work` still confirm the selected
mode, and `$p:managing-skill-activation` reports it on demand. Add the footer
label later only if Codex documents a custom-text or active skill-profile item.

## Gate and failure contract

The internal command contract follows the repository-wide exit convention:

- `0` — the component or capability is enabled;
- `1` — policy was read cleanly and the component or capability is disabled;
- `2` — policy or controller could not be evaluated.

Every skill disabled cleanly by exit `1` stops before any task-specific tool
call, file read, transcript discovery, network lookup, or mutation. On policy
error, sensitive whole-skill and conditional-capability checks exit `2` and
stop; `core` and `control-plane` checks exit `0` with a warning. Exit `1`
reports the active profile and the exact enable command. Exit `2` reports a
controller/configuration fault and does not continue optimistically into
governed data.

Failure is contained:

- the controller is never called from a hook;
- core shell, file, Git, and harness operation continue normally;
- an unreadable or malformed state file cannot block a prompt;
- a failed global write leaves the old file byte-for-byte intact;
- an unknown component or capability fails closed;
- stale overrides for removed components are warnings in `status`, not a
  reason to break unrelated skills;
- direct execution of a plugin script remains possible for tests and explicit
  repository policy. Activation profiles govern skill invocation, not the
  shell as a whole.

The last point keeps the boundary honest. This feature prevents accidental
skill-triggered work; it is not a sandbox and must never be described as one.

## Packaging and update behavior

- Keep a single marketplace entry and a single plugin namespace. Splitting the
  plugin would make disabled skills truly absent, but would either rename
  invocations, duplicate core files, or require profile-specific marketplace
  variants.
- The canonical manifest and controller are committed and versioned, so every
  global installation of a given plugin version has identical policy logic.
- Global selection and overrides stay outside both the checkout and installed
  cache, so a normal update/reinstall preserves them. Session selections remain
  deliberately tied to their original session ID; a new session starts from
  the environment lock, global selection, or `home` default.
- Explicit `sync-native` may add exact-path Codex `skills.config` entries for
  future-session catalog visibility. Those entries are owned adapter output,
  not canonical state. It replaces stale owned paths after an update and never
  edits unrelated skill entries. It is optional because catalog hiding prevents
  a running session from hot-switching those entries back with `$p:home`.
- The status-label bundle is copied to the existing stable statusline state
  directory. The bundled renderer and owned ccstatusline widget do not point
  into a versioned plugin cache and therefore keep working across a routine
  plugin update; the next toggle or statusline apply refreshes the copied
  version transactionally.
- A release bumps the plugin version and keeps the marketplace and plugin
  manifests synchronized. Codex's normal reinstall/cachebuster flow and a new
  thread are still required to load new plugin contents. Claude's normal
  `plugin update` flow still requires a restart. This plan does not pretend to
  hot-reload code the harness already cataloged.
- Once a version containing the controller is loaded, policy changes are hot:
  no restart is required to enable or disable an existing component.

## Migration and rollout

1. Add the policy manifest, controller, validator, and isolated unit tests.
   With no local state, prove that all current components evaluate enabled.
2. Add `$p:home`, `$p:work`, and the management skill; instrument the
   repository-privacy skill first. Dogfood session and global state without
   changing the default.
3. Instrument every source skill and command-derived skill, then add the
   conditional gates to mixed-purpose workflows. Coverage tests make partial
   instrumentation a release failure.
4. Extend the bundled Claude renderer and ccstatusline compatibility path with
   the fail-soft `p:h`/`p:w` indicator. Preserve Codex's native footer.
5. Run cross-harness smoke tests from fresh sessions in both profiles. Verify
   that a disabled invocation stops before its underlying command and that an
   unrelated skill still runs after corrupt-policy and missing-controller
   cases.
6. Bump the minor plugin version, synchronize catalog metadata, run the normal
   release validation, update the remote, then reinstall through each
   harness's supported flow.
7. Home installations need no migration because `home` is the default. A work
   installation can keep `P_SKILL_PROFILE=work` in its launcher, select a
   global work default once, or use `$p:work` per session.
8. Test the optional native adapter after Codex reinstall and verify
   fresh-session catalog visibility. Runtime gates remain authoritative if a
   cache path changes or the operator never opts into catalog hiding.

## Verification plan

### Deterministic tests

- Manifest schema, exact component coverage, and control-plane invariants.
- No-state compatibility: every pre-existing component is enabled.
- `home`, `work`, individual enable/disable, reset, and limited-component
  resolution.
- Precedence: environment over session over global over default, with no
  cross-layer merge.
- Invalid environment value, malformed JSON, unsupported schema version,
  unknown component/capability, unreadable state, and failed atomic replace.
- Exit codes and stable machine-readable JSON output.
- No output includes state-file paths or state contents.
- Session isolation and stale-session cleanup.
- `$p:home` and `$p:work` resolve their launcher scripts relatively, set only
  the current session, cannot disable the control plane, and refuse rather than
  misreport when a conflicting environment lock is active.
- Every skill and command begins with the required gate; every declared
  conditional capability has a corresponding operation-level gate.
- Bundled-renderer labels for home, work, missing state defaulting to home, and
  malformed state rendering unknown; a label failure never removes the other
  status information.
- ccstatusline owned-widget add, update, idempotence, rollback, unrelated-widget
  preservation, and invalid-configuration refusal.
- Codex native exact-path adapter generation, stale-path replacement, unrelated
  `skills.config` preservation, and no change to `tui.status_line`.
- Hook configuration is unchanged.

### End-to-end checks

- In `home`, invoke the repository privacy skill and confirm its existing
  workflow is reached.
- Invoke `$p:work` in the same session, confirm `p:w` appears in Claude's
  statusline, invoke the privacy skill again, and confirm no audit command or
  repository scan runs.
- Invoke `$p:home`, confirm `p:h`, and verify the privacy skill is available
  again without a restart.
- In `work`, invoke the evaluation-taxonomy review skill and confirm it stops
  before discovering local evidence or opening its loopback review workspace.
- Confirm mechanical format maintenance still runs in `work`, while its
  history-audit branch stops.
- Corrupt the local policy and confirm the governed skill stops, the user's
  prompt survives, and an unrelated core skill still runs.
- Reinstall/update the plugin and confirm the global profile remains selected.
- Start fresh Claude Code and Codex sessions and confirm their effective
  component lists agree.
- With ccstatusline active, confirm every pre-existing row, widget, style, and
  setting is semantically identical except for the one tagged profile widget.
  In Codex, confirm the aligned native footer remains byte-equivalent.

### Repository checks

```text
git diff --check
python3 -m unittest discover -s plugins/p/tests -t plugins/p/tests
plugins/p/bin/format-e2e
plugins/p/bin/repo-privacy-audit
```

The release is blocked if the privacy audit does not report zero for every
non-exempt category across all six places.

## Alternatives rejected for v1

### Split into core and home plugins

This is the only present-day way to make disabled skills completely absent
from both catalogs. It also changes namespaces for moved skills, makes global
installation a multi-plugin operation, conflicts with the repository's current
single-plugin distribution rule, and complicates migration. Keep it as the
fallback if catalog invisibility becomes more important than namespace and
installation continuity.

### Publish home and work editions

Two generated packages with the same logical plugin would preserve skill names
but introduce duplicate artifacts, edition-specific marketplaces, and update
drift. It also makes switching profiles a reinstall-and-restart operation.

### Rewrite the installed cache

Cache edits are local, overwritten by updates, hard to audit, and contrary to
the remote-as-canonical requirement.

### Inject a disabled list from a hook

A hook can influence model selection but cannot remove a skill or enforce a
gate. It also places profile-controller failure on session/prompt lifecycle—the
exact blast radius this design must avoid.

### Static explicit-only metadata

`disable-model-invocation` and `allow_implicit_invocation` are useful for skills
that should be manual everywhere. They cannot express a per-installation
home/work choice and do not implement a true disabled state.

## Acceptance criteria

Implementation is complete when:

1. a fresh or upgraded installation with no profile state behaves exactly as
   today;
2. selecting `work` disables the six whole skills above and the two
   history-reading branches in both harnesses;
3. `$p:home` and `$p:work` switch the current session with no arguments, and
   Claude shows `p:h` or `p:w` without disturbing existing status-line content;
4. any non-control component can be explicitly enabled or disabled without
   editing the repository or installed cache;
5. a policy change affects the next invocation in the current session;
6. a malformed policy stops only the governed operation and never blocks a
   prompt, session, or unrelated skill;
7. update/reinstall preserves global selection, the owned ccstatusline
   indicator, and unrelated native harness configuration;
8. all component coverage, unit, end-to-end, formatting, and privacy checks
   pass; and
9. no private path, workplace identifier, or session-derived content enters
   tracked files, fixtures, output, or commit metadata.
