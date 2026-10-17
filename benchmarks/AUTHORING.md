# Benchmark case authoring log

Corpus: `src/mqtt_spec_mcp/benchmark/data/cases-v5.0.jsonl` (120 MQTT 5.0 cases:
35 lookup, 30 normative, 25 applied, 15 synthesis, 15 edge).

## Who and when

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

## Sources

The only reference was the OASIS MQTT Version 5.0 publication (OASIS Standard,
07 March 2019), read through this project's locally bundled index. Agents read
sections with `mqtt_spec_get`, `mqtt_spec_search` and `mqtt_spec_lookup`, or
directly from the in-memory section text. Each case's `provenance` field names the
sections its answer key came from.

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
- Case ids describe the question (`<area>-<topic>`).

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
