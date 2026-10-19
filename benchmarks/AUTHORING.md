# Benchmark case authoring log

The corpus has three files under `src/mqtt_spec_mcp/benchmark/data/`:

- `cases-v5.0.jsonl`: 120 MQTT 5.0 cases (35 lookup, 30 normative, 25 applied,
  15 synthesis, 15 edge);
- `cases-v3.1.1.jsonl`: 60 MQTT 3.1.1 cases (18 lookup, 15 normative, 12 applied,
  8 synthesis, 7 edge);
- `cases-compare.jsonl`: 20 cases about how the two versions differ or agree.

## Who and when

### MQTT 5.0 set

- **Date:** 2026-10-16.
- **Authors:** four AI agent instances (GitHub Copilot coding agents), each writing
  one slice of the corpus:
  - 35 lookup cases;
  - 30 normative cases;
  - 25 applied cases;
  - 15 synthesis and 15 edge cases.
- **Reviewers:** two further, independent agent instances. Each checked 60 answer
  keys against the cited sections of the specification. A third instance applied
  the findings. The maintainer agent then integrated the corpus and resolved the
  remaining items.

### MQTT 3.1.1 and comparison sets

- **Date:** 2026-10-19.
- **Authors:** three AI agent instances (GitHub Copilot coding agents):
  - 33 MQTT 3.1.1 lookup and normative cases;
  - 27 MQTT 3.1.1 applied, synthesis and edge cases;
  - 20 comparison cases.
- **Reviewers:** three further, independent agent instances: two checked 30 MQTT
  3.1.1 cases each, one checked the 20 comparison cases. Further instances applied
  the findings, and also checked every key against real model answers from the
  pilot and the first full run, shown without the condition that produced them.

## Sources

The only references were the two OASIS publications listed in the README: MQTT
Version 5.0 (OASIS Standard, 07 March 2019) and MQTT Version 3.1.1 (OASIS
Standard, 29 October 2014), read through this project's locally bundled indexes.
Agents read sections with `mqtt_spec_get`, `mqtt_spec_search` and
`mqtt_spec_lookup`, or directly from the in-memory section text. Each case's
`provenance` field names the sections its answer key came from; in comparison
cases every fact, forbidden claim and citation names its version.

The authors did not read this repository's `tests/` directory, so the benchmark
stays independent of the unit tests.

## Authoring rules applied

- Prompts and search queries are written in the authors' own words, phrased as a
  practitioner would ask. The validator rejects any 8-word run copied verbatim from
  the specification.
- Every expected fact and forbidden claim names the section that grounds it. The
  validator requires at least one regex alternative of every fact to match that
  section's own text.
- Every case has a reference answer. That answer must pass the scorer within the
  case's word limit.
- Lookups and search queries that reuse unit-test scenarios are rejected.
- Comparison prompts must name both versions. In comparison cases each fact is
  grounded in its own version's section, and forbidden claims target rules
  carried over to the wrong version.
- Case ids describe the question (`<area>-<topic>`) and are unique across files;
  some MQTT 3.1.1 ids carry a `v311-` prefix.

## Review outcome

Both reviewers found **no incorrect specification facts, normative strengths or
requirement labels**. They reported 12 errors and 74 warnings, all about scoring
robustness:

- Forbidden-claim regexes that also matched correct, negated phrasings. This led to
  scorer 1.1.0, where a forbidden claim counts only when it is affirmed.
- Fact regexes too narrow for natural phrasing.
- Facts that the prompt did not ask for.
- A few misleading reference sentences.

All errors and 73 warnings were addressed; skipped warnings were checked against
the spec first. The corpus was revalidated (120 cases, 0 problems) before any
scored run.
