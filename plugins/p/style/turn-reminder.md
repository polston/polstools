Format reminder: a turn-ending reply begins with the literal line `# FINDINGS`,
then `# PROBLEMS`, then `# ASKS`. All three sections are present; an empty one
is `**1.** None.` Add `---` only when optional detail follows. An interim
message immediately before a tool call is one concise status sentence with no
headers or reader question; repeat actionable items in the final reply.

Use bold number prefixes, never markdown lists. Keep at most five non-`None`
top-level items total and each substantive line at most 30 words; problem
titles stay under 15. Every PROBLEM has a reader consequence and recommended
action. Questions needing answers appear only in ASKS. Unresolved PROBLEM and
ASK numbers persist without reuse; FINDINGS restart at 1 each final reply.
