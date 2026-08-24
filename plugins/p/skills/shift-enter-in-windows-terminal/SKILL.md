---
name: shift-enter-in-windows-terminal
description: Use on Windows when Shift+Enter submits instead of inserting a newline in a terminal-based agent CLI, or when /terminal-setup reports that Windows Terminal needs no configuration and writes nothing.
---

# Shift+Enter in Windows Terminal

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check shift-enter-in-windows-terminal`.
If it exits 1 or 2, stop and report its output.

## Overview

`/terminal-setup` refuses to configure Shift+Enter under Windows Terminal — it
treats Windows Terminal as having native support and writes no configuration.
Native Shift+Enter still submits, because the CR it emits is the submit key.

The fix is a Windows Terminal keybinding, not an agent setting.

## Root cause

| Key | Byte | Effect |
|---|---|---|
| `Enter` | `0x0D` (CR) | submits |
| `Ctrl+J` | `0x0A` (LF) | inserts a newline |
| `Shift+Enter`, unconfigured | `0x0D` | submits — the problem |

So the fix is to make Shift+Enter send `0x0A` instead.

## The fix

Edit Windows Terminal's settings:

```
%LOCALAPPDATA%\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json
```

Add a keybinding mapping `shift+enter` to `sendInput` with the input `"\n"` —
a raw line feed, not `\r` and not `\r\n`.

```json
{ "keys": "shift+enter", "command": { "action": "sendInput", "input": "\n" } }
```

Windows Terminal normalizes this into an `actions[]` entry plus a
`{ "id", "keys" }` reference on save. That rewrite is expected, not corruption
— do not undo it.

**Takes effect in new tabs.** An open tab keeps the old binding.

## Zero-config fallback

`Ctrl+J` always inserts a newline, with no configuration anywhere. Use it while
testing, and as the answer for a machine you do not want to configure.

## Verifying

Open a **new** tab, then press Shift+Enter at a prompt. A newline appears and
nothing submits. If it still submits, the tab predates the change.

## Common mistakes

**Using `\r` or `\r\n` as the input.** Both carry the CR that submits. Only a
bare `\n` works.

**Testing in the tab you already had open.** Bindings load per tab.

**Assuming `/terminal-setup` did something.** Under Windows Terminal it exits
successfully having written nothing — success is not evidence of a change.
