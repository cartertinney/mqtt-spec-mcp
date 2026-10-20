# mqtt-spec-mcp

An offline [Model Context Protocol](https://modelcontextprotocol.io) server that lets
AI agents navigate, search and quote short, authoritative excerpts of the OASIS
**MQTT 3.1.1** and **MQTT 5.0** specifications without loading either full
specification into their context.

- Four read-only tools: `mqtt_spec_outline`, `mqtt_spec_search`, `mqtt_spec_get`,
  `mqtt_spec_lookup`.
- Two bundled, pre-built indexes; no network, database, embeddings or LLM at query time.
- **Strict version isolation.** Every call names exactly one version. Nothing is
  inferred, defaulted, merged or carried over between 3.1.1 and 5.0.
- Small responses and a small tool surface: one search returns an evidence pack of
  about 6,000 characters drawn from several sections (up to about 11,000 for a
  question with several parts), `get` pages never exceed `max_chars`, and the tool
  definitions plus instructions an agent sees cost about 560 tokens per model call.

The server returns source excerpts. The agent does the reasoning, and the OASIS
publications remain the authoritative text.

## Supported specifications

| Version | Publication | Published | Source |
| --- | --- | --- | --- |
| 3.1.1 | MQTT Version 3.1.1, OASIS Standard (editors A. Banks, R. Gupta) | 2014-10-29 | [mqtt-v3.1.1-os.html](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html) |
| 5.0 | MQTT Version 5.0, OASIS Standard (editors A. Banks, E. Briggs, K. Borgendale, R. Gupta) | 2019-03-07 | [mqtt-v5.0-os.html](https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html) |

3.1.1 is indexed from the original 2014 OASIS Standard HTML, not the Errata 01
consolidated edition, and no errata are applied. That publication names its `.doc`
edition as authoritative. MQTT 3.1 (pre-OASIS) is not supported.

## Install and run

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10 or newer. The package is
not published to PyPI; run it from a checkout:

```bash
git clone https://github.com/cartertinney/mqtt-spec-mcp.git
cd mqtt-spec-mcp
uv sync
uv run mqtt-spec-mcp          # stdio MCP server (python -m mqtt_spec_mcp also works)
```

The server needs no environment variables, config files or flags. Both indexes are
loaded and validated once at startup, which takes about half a second from process
launch to the first result.

## Client configuration

Generic `mcpServers` configuration (Claude Desktop, Claude Code, Cursor and similar):

```json
{
  "mcpServers": {
    "mqtt-spec": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/mqtt-spec-mcp", "run", "mqtt-spec-mcp"]
    }
  }
}
```

VS Code (`.vscode/mcp.json`):

```json
{
  "servers": {
    "mqtt-spec": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/mqtt-spec-mcp", "run", "mqtt-spec-mcp"]
    }
  }
}
```

This repository also contains both files pre-filled for working in the checkout
itself: `.vscode/mcp.json` (VS Code, including agent-host sessions) and `.mcp.json`
(Claude Code and other clients that read project-scoped servers). For Claude Code,
`.claude/settings.json` allows the four read-only tools to run without a permission
prompt.

After upgrading, restart the server and start a new conversation so the client
drops any cached tool schemas.

## Tools

Every tool requires `version`, which must be exactly `"3.1.1"` or `"5.0"`. It has no
default, and `"5"`, `"v3.1.1"`, `"latest"`, `5` and similar values are rejected.

JSON results (outline, lookup) start with the same envelope; search results are
Markdown and start with the same scope sentence and source URL:

```json
{"specification":"MQTT Version 5.0","version":"5.0",
 "scope":"MQTT 5.0 only. Never apply its section numbers, labels, fields or codes to MQTT 3.1.1.",
 "source":"https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html", ...}
```

Each tool advertises only the parameters agents use, because every model call
re-sends the tool definitions; the other parameters below are still accepted.

Errors are returned as MCP tool errors (`isError: true`). Each message starts with
`MQTT <version> only (<source URL>):`, says what was wrong and what to do instead,
and ends with `No other version was queried.` Any free-text argument that names the
other version (for example `"MQTT 5.0 Session"` when `version` is `"3.1.1"`) is
rejected with a message that refers to the *selected version*.

### `mqtt_spec_outline`

`version`, `parent` (section id or unambiguous title, optional); `depth` (1–5,
default 1) is accepted. Returns section ids and titles only, with no body text.

```json
{"version": "5.0", "parent": "3"}
```

This returns the 15 packet sections `3.1` (CONNECT) through `3.15` (AUTH); the same
call with version 3.1.1 returns 14.

### `mqtt_spec_search`

`version` and `query`: the question's key terms, a section id (`3.1.2.4`) or a
requirement label (`MQTT-3.1.2-23`). For a question with several parts, give one
group of key terms per part, separated by `;`. Everyday spellings are matched in
the specifications' terms: `keepalive` as Keep Alive, `LWT` and `last will` as Will,
`broker` as Server, `QoS0` as QoS 0, `client id` as ClientID and `kick` as
disconnect. `chapter` (restrict to a section's
subtree), `limit` (1–8 sections, per part; giving it also leaves out related
requirements) and `normative_only` are accepted.

```json
{"version": "5.0", "query": "Topic Alias 0 or above Topic Alias Maximum"}
{"version": "5.0", "query": "Receive Maximum send quota; Receive Maximum default; QoS 0 Receive Maximum; Receive Maximum exceeded"}
```

The result is a Markdown evidence pack of about 5,500 characters: query-focused
excerpts from up to eight of the best-ranked sections, each under a `## <id> <title>`
heading, in rank order. The sentences that carry the most query information across
those sections are taken first, each with a neighbouring sentence for context; a
sentence from a list also brings the list's lead-in, and the whole list when it is
at most 1,200 characters. Normative sentences (MUST, SHOULD, MAY or a requirement label) count double, and the
best-ranked section's remaining sentences fill spare room before weak matches
elsewhere. `...` marks every cut, and a sentence repeated word for word in a later
section is shown once. Up to 700 more characters follow as related requirements:
labelled requirement sentences (`[MQTT-...]`) from sections ranked below those eight
that share at least two of the query's terms, most informative first, each under
its own section heading. They catch a rule stated in a short section, such as
PINGREQ Actions ("The Server MUST send a PINGRESP packet in response to a PINGREQ
packet"), that ranks low as a section. A requirement-label query shows the
requirement in its home section and the sections that list it; a section-id query
shows that section from its start.

A query with several `;`-separated parts (up to four; more are joined to the
fourth) is answered with one pack. Each part is ranked and excerpted on its own
share of 9,000 characters, at least 2,500, so each part of a question gets the
rules that use its own words. A section chosen by several parts appears once with
all their sentences, a sentence is shown once, and related requirements use all
the parts' terms. A requirement-label or section-id part shows what a search for it
alone would.

```text
MQTT 5.0 only. Never apply its section numbers, labels, fields or codes to MQTT 3.1.1. Source: https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html

## 3.3.2.3.4 Topic Alias
35 (0x23) Byte, Identifier of the Topic Alias. ...

## 3.3.4 PUBLISH Actions
...If the PUBLISH packet contains a Topic Alias, the receiver processes it as follows:
- 1) A Topic Alias value of 0 or greater than the Maximum Topic Alias is a Protocol Error, ...

Answer from these excerpts: state each rule here that bears on the question, with its MUST/SHOULD/MAY and conditions, and cite its section. If a needed rule is missing, get one listed section with mqtt_spec_get (version='5.0'). Do not search MQTT 5.0 again for this question (to compare versions, search MQTT 3.1.1 once).
```

### `mqtt_spec_get`

`version`, `section` (an id such as `3.1.2.4`, an id plus title, or an unambiguous
title), `cursor`; `include_subsections` and `max_chars` (1,000–12,000, default
4,000) are accepted.

```json
{"version": "3.1.1", "section": "3.1.2.4"}
```

```text
# 3.1.2.4 Clean Session

MQTT 3.1.1 ONLY - version-specific evidence.

Position: bit 1 of the Connect Flags byte.
...
---
Source: OASIS MQTT Version 3.1.1, section 3.1.2.4 - https://docs.oasis-open.org/...
Truncated: no
```

Tables are rendered as Markdown tables and list items as `- ` bullets. When the
footer says `Truncated: yes`, call again with the same arguments plus the
`Next cursor`. A cursor only works with the version, section, `include_subsections`
and `max_chars` it was issued for. Passing `3.1.2.4 Clean Start` with version 3.1.1
fails with `title does not match`, because that section is Clean Session in 3.1.1
and Clean Start in 5.0.

### `mqtt_spec_lookup`

`version`, `kind`, `query`, `packet` (optional filter). Exact lookups of one entity:

| kind | 3.1.1 | 5.0 | query |
| --- | --- | --- | --- |
| `packet` | 14 packets | 15 packets (adds AUTH) | name or value |
| `property` | not available | 27 properties | id (`0x11`/`17`), name or name fragment |
| `reason_code` | not available | 45 entries | value or name; `packet` filters |
| `return_code` | CONNACK 0–5, SUBACK 0/1/2/0x80 | not available | value or name (each match names its packet); `CONNACK` or `SUBACK` lists that packet's codes; `packet` CONNACK or SUBACK filters |
| `requirement` | 141 labels | 252 labels | label, e.g. `MQTT-3.1.2-4` |
| `term` | 9 glossary terms | 15 glossary terms | name or name fragment |

```json
{"version": "5.0", "kind": "property", "query": "0x11", "packet": "CONNECT"}
{"version": "3.1.1", "kind": "return_code", "query": "0x80", "packet": "SUBACK"}
```

The first returns Session Expiry Interval (Four Byte Integer) with an excerpt of
section 3.1.2.11.2. The second returns **Failure**, whereas in 5.0 SUBACK `0x80` is
the reason code **Unspecified error**.

Results carry the spec text needed to answer about the entity without a `get`:

- A packet has a `summary` (the start of its section), the prose of its fixed
  header section as `fixed_header` (for example DISCONNECT's reserved-bits rule),
  `remaining_length` when the fixed header fixes it (PINGREQ: 0), and the start of
  its Actions or Response sections as `actions` (PINGREQ: the Server MUST send a
  PINGRESP).
- A property has excerpts of the sections that define it: for `packet`'s section
  when given, otherwise for each defining section when there are at most three.
- Reason codes (at most three matches) and return codes list their `meanings`: the
  row of each packet's own code table, with the sender where the table names one
  (3.1.1 SUBACK has no such table).

## Version-safety guidance for agents

The server sends these instructions to clients. In short:

- Pass `version` explicitly on every call. If the user has not said which version
  they mean, ask; do not assume the latest.
- Never carry section numbers, requirement labels, field names or numeric codes
  from one version to the other. For example, 3.1.2.4 is Clean Session in 3.1.1 and
  Clean Start in 5.0, and SUBACK 0x80 also means different things.
- To compare versions, run the workflow once per version and cite the version next
  to every claim. A miss in one version says nothing about the other.
- 3.1.1 has Clean Session and return codes. 5.0 has Clean Start, Session Expiry,
  properties and reason codes.

## Recommended workflow

1. Consult the tools before answering. Use `mqtt_spec_lookup` for one exact,
   recognized entity; otherwise search once with the question's key terms, one
   `;`-separated group per part of the question.
2. Answer from the excerpts: state each rule that bears on the question with its
   MUST/SHOULD/MAY and conditions, and cite its section.
3. If a needed rule is missing, get one listed section with `mqtt_spec_get`, and
   follow cursors only if needed. Use `mqtt_spec_outline` only to browse.

## Index provenance and regeneration

The bundled indexes (`src/mqtt_spec_mcp/data/mqtt-v3.1.1.json`,
`mqtt-v5.0.json`) are generated offline from the pinned OASIS HTML:

```bash
uv run python -m scripts.generate_index --version 5.0
uv run python -m scripts.generate_index --version 3.1.1 --source ./mqtt-v3.1.1-os.html
```

The generator downloads (or reads) the source, canonicalizes Cloudflare rewrites,
checks the SHA-256 against the pin for the selected version only, parses it as
Windows-1252, and builds the sections, blocks and catalogs. It writes the output
atomically and only if a strict completeness gate passes:

| Count | 3.1.1 | 5.0 |
| --- | ---: | ---: |
| Numbered sections | 162 | 291 |
| Control packets | 14 | 15 |
| Properties | 0 | 27 |
| Reason-code entries | 0 | 45 |
| Return-code entries | 10 | 0 |
| Requirement labels | 141 | 252 |
| Glossary terms | 9 | 15 |

`--allow-source-mismatch` exists only for deliberately reviewing a changed source.
The server loads each index by package resource, rejects unknown fields, and
requires its metadata to match that version's pin exactly. See
[DECISIONS.md](DECISIONS.md) for the canonicalization procedure, parser choices,
the 3.1.1 label normalizations and the 5.0 label reconciliation.

## Benchmarking

Install the extra (Inspect AI and the GitHub Copilot SDK need Python 3.11 or newer):

```bash
uv sync --extra benchmark
```

The case corpus has 200 questions in three files under
`src/mqtt_spec_mcp/benchmark/data/`:

| File | Version | Cases | Mix |
| --- | --- | ---: | --- |
| `cases-v5.0.jsonl` | 5.0 | 120 | 35 lookup, 30 normative, 25 applied, 15 synthesis, 15 edge |
| `cases-v3.1.1.jsonl` | 3.1.1 | 60 | 18 lookup, 15 normative, 12 applied, 8 synthesis, 7 edge |
| `cases-compare.jsonl` | both | 20 | questions about how the two versions differ or agree |

The single-version sets each cover all seven chapters and every control packet of
their version. See [benchmarks/AUTHORING.md](benchmarks/AUTHORING.md) for how the
cases were written and reviewed.

```bash
# Validate the corpus (schema, grounding, coverage, reference answers)
uv run python -m mqtt_spec_mcp.benchmark.cases

# Deterministic retrieval benchmark (no model); thresholds suit PR gating
uv run mqtt-spec-bench-retrieval --min-recall-at-1 0.70 --min-recall-at-5 0.60 \
    --min-hit-rate 0.90 --min-mrr 0.78 --min-lookup-success 1.0

# Sizes, o200k_base token counts, latency, stdio cold start
uv run mqtt-spec-bench-micro

# Agent benchmark with the GitHub Copilot SDK: a fresh session per case and
# condition, several sessions at once. Re-running the same command resumes: it
# retries failures and re-runs anything whose question, prompt or reference
# material (for mcp, the server) changed since the stored result.
uv run mqtt-spec-bench-copilot --model claude-sonnet-5 --concurrency 6 \
    --output benchmarks/runs/copilot.jsonl
uv run mqtt-spec-bench-copilot --model claude-sonnet-5 --sample 20 --versions 3.1.1 \
    --output benchmarks/runs/pilot.jsonl          # a seeded, stratified pilot

# ... or with Inspect AI (tasks registered as mqtt_spec_mcp/<task>)
uv run inspect eval mqtt_spec_mcp/mqtt_mcp --model openai/gpt-5
uv run inspect eval mqtt_spec_mcp/mqtt_reference -T condition=full_spec -T versions=3.1.1

# Paired report (overall and per version) with bootstrap 95% CIs and release gates
uv run mqtt-spec-bench-report benchmarks/runs/copilot.jsonl \
    --gate-quality-regression 0.02 --gate-token-reduction 0.50 --gate-speedup 1.20
uv run mqtt-spec-bench-report benchmarks/runs/copilot.jsonl --rescore --epoch 1
uv run mqtt-spec-bench-report --inspect-log logs/<file>.eval --rescore
```

`--epoch` restricts the report to runs from the given epoch(s). Compare conditions
only within runs made together: the same model can answer measurably differently a
few hours later (see the results below).

Current retrieval baseline on the 200 cases (220 queries, since each comparison case
is ranked once per version): Recall@1 0.81, Recall@3 0.61, Recall@5 0.68, hit rate
0.98, MRR 0.88, lookup success 1.00, p95 latency under 10 ms. Per version, 3.1.1 has
Recall@1 0.83 / MRR 0.89 and 5.0 has Recall@1 0.81 / MRR 0.88. The CI gate thresholds
sit below this baseline. Recall@k divides by min(|relevant|, k), and cases list about
three relevant sections on average, often in different chapters, so Recall@3 and
Recall@5 are stricter than the hit rate.

The agent benchmark compares four conditions that share the same cases, base
prompt, loop and 12-call tool budget. Each condition reaches the same material, the
specification(s) the question is about:

- `none`: no reference material.
- `full_spec`: the whole text of the relevant specification(s) in the system prompt.
- `grep`: a generic line search plus an 80-line reader over that text, with no
  MQTT catalogs.
- `mcp`: this server's tools, loaded with only the relevant version(s); comparison
  questions get both versions and must keep them apart.

Single-version questions are sent labelled with their version (`[MQTT 3.1.1] …`), as
a user of a multi-version tool would ask them; comparison questions name both.

Scoring is deterministic: `quality = clamp(0.8 × fact recall + 0.2 × citation recall −
violation rate, 0, 1)`. A run passes only with every fact, every citation group and
no forbidden claim. Answers are normalized before matching (markdown emphasis
removed, heading-only lines joined to their paragraph), and a negation only cancels
a fact within its own clause. Every result records hashes of the shared
instructions, the condition's prompt and reference material (for `mcp`, the server:
tools, instructions, code and indexes), and the question asked. The report refuses
to compare runs whose hashes conflict, but one condition can be re-run after a
change and compared against stored results for the others. Answer keys are not part
of the question hash: use `--rescore` to apply corrected keys or a newer scorer to
stored answers offline.

### Latest results

[benchmarks/results/2026-10-19-evidence-pack-claude-sonnet-5.md](benchmarks/results/2026-10-19-evidence-pack-claude-sonnet-5.md)
has the full report. Claude Sonnet 5 through the Copilot SDK, all 200 cases, this
server (with multi-part search) and the grep baseline run side by side: the 102
hard cases (synthesis, applied, edge, comparison) in two epochs and the 98 lookup
and normative cases in one, on answers the answer-key checks never saw. Each case is
averaged over its runs:

| condition | quality | pass rate | tokens/question | cost-weighted tokens | latency (mean) | model calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `mcp` | 0.979 | 89.2% | 7,730 | 7,752 | 8.1 s | 2.06 |
| `grep` | 0.971 | 86.3% | 9,090 | 7,813 | 9.5 s | 2.94 |

Against a plain grep tool over the same text the model was at least as accurate
(+0.8 points, 95% CI -0.4 to +2.0), used 15% fewer tokens (CI 7.4% to 22.1%; 20%
on the hard cases) and was 18% faster. Once cached tokens are priced (cache reads
0.1, cache writes 1.25, output 5) the two cost about the same. It led clearly on
questions comparing the two versions (+5.5 points, CI +1.1 to +10.2). Earlier
same-time passes of the evidence-pack design, and the whole specification in the
prompt (1.6 points more accurate, 11 times the tokens), are in the same report;
[benchmarks/results/2026-10-19-claude-sonnet-5.md](benchmarks/results/2026-10-19-claude-sonnet-5.md)
has the earlier runs, including `none` and the previous search design.

## Development

```bash
uv sync --extra benchmark
uv run ruff check . && uv run ruff format --check .
uv run mypy                                                    # strict, Python 3.10 target
uv run mypy --python-version 3.12 src/mqtt_spec_mcp/benchmark/*.py  # runners
uv run pytest
uv build
```

The test suite includes a real stdio handshake, exhaustive pagination of every
section in both versions at 1,000 characters, parser fixtures, completeness-gate and
loader rejections, and the version-isolation scenarios.

## License

The code is MIT licensed (see [LICENSE](LICENSE)). Specification text in the bundled
indexes and in every excerpt the server returns is © OASIS Open. See
[NOTICE](NOTICE), which reproduces the OASIS copyright and IPR notices of both
publications and explains that the indexes are transformed retrieval aids, not
official or modified publications.
