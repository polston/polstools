# ccstatusline compatibility

Use this path when Claude's `statusLine.command` directly invokes
`ccstatusline`. Preserve that command, its layout, and unrelated widgets.
Alignment means shared information and percentage semantics, not identical
rendering technology. The plugin may add one custom-command widget tagged with
`p.owner=skill-activation-v1`; that widget alone displays `p:h` or `p:w`.

## Recommended configuration

- Keep Claude's richer two-line layout and Codex's native footer.
- Configure ccstatusline's native Context Percentage widget for remaining mode;
  in its TUI, select the widget and press `u`. The persisted metadata is
  `{"inverse":"true"}`.
- Render five-hour and weekly values from Claude's native `rate_limits` input.
  Convert `used_percentage` to `100 - used_percentage`, label the result
  `% left`, and keep any reset countdowns already present.
- A model-scoped weekly value is absent from status-line input. If it is wanted,
  keep credential access and refresh outside ccstatusline's hot path: use the
  platform credential store, single-flight asynchronous refresh, and a short
  timeout. Cache only the label, percentage, reset time, and fetch timestamp;
  mark stale values visibly.
- Never place a credential in the ccstatusline configuration, command line,
  stdout, or cache. Never cache the complete usage response.

## Verification without Claude access

Use ccstatusline's preview for native widgets. Test custom commands with
synthetic stdin and an isolated fresh cache. A sample with context 28% used,
five-hour 36% used, and weekly 19% used must render 72%, 64%, and 81% left.
Do not invoke Claude or a live usage endpoint merely to test presentation.

The default `statusline-ctl sync` and explicit `apply` preserve a directly
configured ccstatusline command, every unrelated widget and setting, and
Codex's native footer contract. They add or refresh only the tagged profile
widget. `check` certifies that owned widget; it cannot certify unrelated custom
commands, so verify those separately as above. An unknown external Claude
renderer is never overwritten.
