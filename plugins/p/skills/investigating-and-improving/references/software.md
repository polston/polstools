# Software correctness

Inspect the actual public boundary and reproduce the claimed failure before
selecting a repair where feasible. Compare mechanism-level explanations, including
stale inputs, invalid assumptions and integration failures. Choose a probe that
separates them. A symptom workaround can be a useful diagnostic without being the
best retained fix.

Tests should detect relevant deliberately faulty alternatives and boundary inputs.
Preserve other quality conditions: a cache fix that disables caching may restore
values while losing the required reuse. Separate structural conformance, measured
performance and user benefit. Use real command results and exact code/input
versions; do not treat an unrelated green suite as evidence for the claim.

For architecture choices, include a credible simpler design and an explicit future
change or recovery challenge. More extension points do not establish usefulness.
Prefer the existing implementation when a candidate's benefit is unsupported.
