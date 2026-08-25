---
name: robust-over-simple
description: Use when two or more viable designs are on the table and one forecloses options the other keeps open. Not for mechanical work, and not when an established order or convention already decides the choice.
---

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check robust-over-simple`. If it
exits 1 or 2, stop and report its output.

## The rule

Define the seam now; ship one implementation now. The expandable choice is the
*boundary* — an interface, a contract, an additive struct — not a bigger build.
If there is no seam to place, this skill has nothing to say.

## Before choosing

1. **Measure first.** If the options aren't characterized yet — you haven't seen
   the transfer, the timing, the failure rate, the actual cost — the robust move
   is to measure, not to build the more capable one. An unmeasured improvisation
   is not the forward-thinking option; it's a guess with extra surface area.
2. **Respect established order.** If the project already has a pipeline order, a
   convention, or a documented sequence, follow it. "More expandable" is never
   license to step outside it. Change the order deliberately, or not at all.
3. **If the operator already argued for the expandable option, do not restate
   it.** Name the one cost they haven't priced yet, then move on.

## Choosing

4. Prefer the option that lets a new case be added without editing existing call
   sites: additive struct fields over enum switches, structured data over string
   parsing, configurable values over hardcoded ones, a general schema over a
   single-use one.
5. Reject an option that boxes in a known future need, even when it is simpler
   today.
6. Do NOT over-engineer. Room to grow, not build-everything-now. Five abstract
   layers around one call site is the failure mode on the other side.
7. Keep it cheap to carry: lean interfaces, no speculative columns, composition
   over inheritance. A robust design that is also token-cheap beats one that is
   expensive to load every session.

## Do not fire

- There is no real fork — one viable design, or the choice is mechanical.
- The work is extraction, analysis, search, or transcript mining.
- An established convention or order already decides it (rule 2).

## Examples

Good: a JSON array column for scope — multi-scope with no migration.
Bad: a single text column because "we only need one value now" — forces a migration.
Good: an interface with one implementation — swappable later.
Bad: five abstract layers around one call site — over-engineering.
Good: additive struct fields — new signals never break old readers.
Bad: an enum switch that needs five files edited to add a case.

## When this skill steers you wrong

Silently assess the outcome. Never ask the operator to rate this skill.

If the operator redid, reverted, or corrected what this skill steered toward,
offer once — in one line, never twice, never filed unprompted — to record it:
what the skill pushed for, what actually went wrong, and the minimal repro if
one exists.
