# Benchmark case authoring log

The corpus has three files under `src/mqtt_spec_mcp/benchmark/data/`:

- `cases-v5.0.jsonl`: 120 MQTT 5.0 cases (35 lookup, 30 normative, 25 applied,
  15 synthesis, 15 edge);
- `cases-v3.1.1.jsonl`: 60 MQTT 3.1.1 cases (18 lookup, 15 normative, 12 applied,
  8 synthesis, 7 edge);
- `cases-compare.jsonl`: 20 cases about how the two versions differ or agree.

This log covers that bundled corpus, which was used to develop the server.
Independent corpora (corpus format 2, schemas in [schema/](schema/)) are written
outside this repository, from a brief that bars their authors from opening this
server, its benchmark or any other MQTT question-answering tool; see the README's
section on independent corpora.

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
3.1.1 has since been re-indexed from the edition with Errata 01 applied; that
erratum changes only the pseudocode in section 2.2.3, which no case relies on.
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

### MQTT 5.0 set

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

### MQTT 3.1.1 and comparison sets

The reviewers reported 62 errors and 17 warnings across the 80 cases. Several
reference answers needed corrections or qualifications (for example, a comparison
answer said 5.0 "adds" CONNACK codes that are the 5.0 forms of 3.1.1 return codes,
and a 3.1.1 answer said clients "must rely on TLS" where the specification also
lists a VPN), and three comparison prompts were reworded and their cases re-run:
one had the CONNACK direction backwards, one did not ask about the 3.1.1 behaviour
its key checked, and one could be read two ways. Most findings were about scoring
robustness:

- Fact regexes that rejected the prompt's own wording, tables, bullet lists or
  reversed order, and windows that stopped at the dot in an inline section number.
- Fact regexes that credited wrong answers, mostly through a negation inside the
  matched text ("the Will is not published").
- Forbidden claims that flagged correct contrasts with the other version
  ("MQTT 5.0: the server sends DISCONNECT ...").
- Facts and citation groups that the prompt did not ask for.

Scorer-level causes were fixed in the scorer (1.3.0 and 1.3.1, see DECISIONS.md),
the rest in the keys.

### Checking keys against real answers

After the first full agent run, every case's key (all 200) was also checked
against the four stored answers to it, shown without the condition that produced
them: a failing answer that was correct for a fact led to a broader key, and a
passing answer with wrong content to a tighter one or a new forbidden claim.
Facts that only restated the prompt, or demanded an optional detail (0x82 for a
repeated CONNECT is MAY/SHOULD), were dropped. Across the 985 distinct stored
answers this turned 311 failing answers into passes and 3 passes into failures
(an answer claiming a 3.1.1 Server MUST accept an empty ClientId, one credited only
through text about the other version, and one with a wrong reason code); every
remaining failure was checked as a real error or omission. As a check on leniency,
each case was scored against 5 reference answers of other cases of the same
version: 3 of 1,000 passed, all on near-duplicate topics.

A second pass checked the keys against the answers from the first two
evidence-pack runs (1,000 new distinct answers, again without their conditions),
with the same rules. It changed 88 cases; across all 3,692 distinct stored answers
194 failing answers became passes and 3 passes became failures (a SHOULD where the spec says MUST, a maximum packet size of
268,435,457 bytes instead of 268,435,460, and an answer credited only through a
negated phrase). Three facts the prompts do not ask for were dropped (a refusal
path, re-authentication, and a 3.1.1 rule the prompt states as given); two wrong
claims seen in answers became forbidden claims; 10 facts that credited prompt
echoes or text about the other version were tightened.

A third pass, with the same rules, checked the keys against the 800 answers of the
next two runs. It changed 50 cases: across the 3,692 distinct answers stored by
then, 48 failing answers became passes and 4 passes became failures (wrong content
that had been credited through an unrelated sentence, a code name read as the
failure rule, and a reason code the Server may not send). Seven facts that only
echoed the prompt (for example "QoS 2" for a question about the QoS 2 handshake)
were dropped, as was one 3.1.1 behaviour the prompt states as given. The 800
answers of the run that followed, which no pass had seen, gained 10 passes and lost
none from these changes.
