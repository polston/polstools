# Changelog

Notable changes to the `p` plugin. Each version heading matches the `version`
field of the plugin manifests; changes made before this file existed are in the
git history.

## [Unreleased]

- CI checks out full history, pins every action to a commit, runs on Linux,
  macOS, and Windows under Python 3.9 and 3.14, runs the stopped-promises
  self-test and a shell syntax check, checks whitespace across the whole
  committed tree, runs `p-validate --base` on pull requests, and has a job that
  runs the evaluation tests with the optional analytics packages required.
- README covers Antigravity in every install, update, and development section
  and adds uninstall commands. A `LICENSE` file is added.
- The response-format hook enters through `bin/format-gate`, which settles the
  off case in the shell and cannot block a prompt, and a Python 3.9 floor is
  declared. Antigravity gets a response-format hook, a read-only status line
  check, one plugin-root convention in every skill, and a hook contract in the
  validator and doctor.
- `p-update` reads every harness before changing any, reinstalls Antigravity
  from the copy another harness loads or from `--agy-source`, and offers
  `--dry-run`. `p-validate` runs one relocated copy and, with `--base`, requires
  a version increase.
- The repository privacy audit covers more categories and refuses a shallow
  clone. The Claude status line renderer is Python and fits the terminal width.
- Antigravity token usage is reported as not measured, and the evaluation layer
  has an Antigravity adapter.

## [1.10.1]

First changelog entry. The tree at this version is the baseline.
