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
- Size limits on every response: `get` pages never exceed `max_chars`, and default
  search results stay well under 5,000 characters.

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

After upgrading, restart the server and start a new conversation so the client
drops any cached tool schemas.

## Tools

Every tool requires `version`, which must be exactly `"3.1.1"` or `"5.0"`. It has no
default, and `"5"`, `"v3.1.1"`, `"latest"`, `5` and similar values are rejected.

JSON results (outline, search, lookup) start with the same envelope:

```json
{"specification":"MQTT Version 5.0","version":"5.0",
 "scope":"MQTT 5.0 only. Do not apply this evidence, ... to MQTT 3.1.1 or any other version.",
 "source":"https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html", ...}
```

Errors are returned as MCP tool errors (`isError: true`). Each message starts with
`MQTT <version> only (<source URL>):`, says what was wrong and what to do instead,
and ends with `No other version was queried.` Any free-text argument that names the
other version (for example `"MQTT 5.0 Session"` when `version` is `"3.1.1"`) is
rejected with a message that refers to the *selected version*.

### `mqtt_spec_outline`

`version`, `parent` (section id or unambiguous title, optional), `depth` (1–5,
default 1). Returns section ids and titles only, with no body text.

```json
{"version": "5.0", "parent": "3"}
```

This returns the 15 packet sections `3.1` (CONNECT) through `3.15` (AUTH); the same
call with version 3.1.1 returns 14.

### `mqtt_spec_search`

`version`, `query`, `chapter` (restrict to a section's subtree, optional), `limit`
(1–5, default 3), `normative_only` (only sections with requirement labels).

```json
{"version": "5.0", "query": "Shared Subscription", "chapter": "4"}
```

The first result has a long snippet (about 1,600 characters), often enough to answer
on its own, plus a `parent_context` snippet. Later results have short snippets (about
320 characters). `requirements` lists the labels in each snippet, and `best_section`
and `next_step` say what to do next. A query can be prose, a section id (`3.1.2.4`)
or a requirement label (`MQTT-3.1.2-23`).

### `mqtt_spec_get`

`version`, `section` (an id such as `3.1.2.4`, an id plus title, or an unambiguous
title), `include_subsections`, `max_chars` (1,000–12,000, default 4,000), `cursor`.

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
| `return_code` | CONNACK 0–5, SUBACK 0/1/2/0x80 | not available | value or name; `packet` CONNACK or SUBACK is required |
| `requirement` | 141 labels | 252 labels | label, e.g. `MQTT-3.1.2-4` |
| `term` | 9 glossary terms | 15 glossary terms | name or name fragment |

```json
{"version": "5.0", "kind": "property", "query": "0x11", "packet": "CONNECT"}
{"version": "3.1.1", "kind": "return_code", "query": "0x80", "packet": "SUBACK"}
```

The first returns Session Expiry Interval (Four Byte Integer) with an excerpt of
section 3.1.2.11.2. The second returns **Failure**, whereas in 5.0 SUBACK `0x80` is
the reason code **Unspecified error**.

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

1. Pick one starting tool. Use `mqtt_spec_lookup` only for one exact, recognized
   entity; use a single combined `mqtt_spec_search` for everything else.
2. If the lookup result or the first search snippet contains the facts, answer.
3. Otherwise call `mqtt_spec_get` once for `best_section`, and follow cursors only
   if needed. Use `mqtt_spec_outline` only to browse or resolve ambiguity.
4. Keep MUST/SHOULD/MAY and every condition and exception, and cite sections.

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

The case corpus (`src/mqtt_spec_mcp/benchmark/data/cases-v5.0.jsonl`) has 120 newly
authored MQTT 5.0 questions: 35 lookup, 30 normative, 25 applied, 15 synthesis and
15 edge. Together they cover all seven chapters and all 15 packets. See
[benchmarks/AUTHORING.md](benchmarks/AUTHORING.md) for how the cases were written and
reviewed.

```bash
# Validate the corpus (schema, grounding, coverage, reference answers)
uv run python -m mqtt_spec_mcp.benchmark.cases

# Deterministic retrieval benchmark (no model); thresholds suit PR gating
uv run mqtt-spec-bench-retrieval --min-recall-at-1 0.70 --min-recall-at-5 0.60 \
    --min-hit-rate 0.90 --min-mrr 0.78 --min-lookup-success 1.0

# Sizes, o200k_base token counts, latency, stdio cold start
uv run mqtt-spec-bench-micro

# Agent benchmark with the GitHub Copilot SDK (fresh session per case and condition)
uv run mqtt-spec-bench-copilot --model gpt-5 --epochs 1 --output benchmarks/runs/copilot.jsonl

# ... or with Inspect AI (tasks registered as mqtt_spec_mcp/<task>)
uv run inspect eval mqtt_spec_mcp/mqtt5_mcp --model openai/gpt-5
uv run inspect eval mqtt_spec_mcp/mqtt5_reference -T condition=full_spec

# Paired report with bootstrap 95% CIs and optional release gates
uv run mqtt-spec-bench-report benchmarks/runs/copilot.jsonl \
    --gate-quality-regression 0.02 --gate-token-reduction 0.50 --gate-speedup 1.20
uv run mqtt-spec-bench-report --inspect-log logs/<file>.eval --rescore
```

Current retrieval baseline on the 120 cases: Recall@1 0.75, Recall@3 0.55, Recall@5
0.64, hit rate 0.93, MRR 0.82, lookup success 1.00, p95 latency under 5 ms. The CI
gate thresholds sit just below this baseline. Recall@k divides by
min(|relevant|, k), and cases list about three relevant sections on average, often in
different chapters, so Recall@3 and Recall@5 are stricter than the hit rate.

The agent benchmark compares four conditions that share the same cases, base
prompt, loop and 12-call tool budget:

- `none`: no reference material.
- `full_spec`: the whole 5.0 text in the system prompt.
- `grep`: a generic line search plus an 80-line reader with no MQTT catalogs.
- `mcp`: this server's tools, pinned to 5.0.

Scoring is deterministic: `quality = clamp(0.8 × fact recall + 0.2 × citation recall −
violation rate, 0, 1)`. A run passes only with every fact, every citation group and
no forbidden claim. Every result records hashes of the prompts, the benchmark
definition and the server definition, and the report refuses to compare runs whose
hashes differ. Use `--rescore` to apply a newer scorer to stored answers offline.

## Development

```bash
uv sync --extra benchmark
uv run ruff check . && uv run ruff format --check .
uv run mypy                                                    # strict, Python 3.10 target
uv run mypy --python-version 3.12 src/mqtt_spec_mcp/benchmark/*.py  # runners
uv run pytest
uv build
```

The test suite covers the acceptance criteria. It includes a real stdio handshake,
exhaustive pagination of every section in both versions at 1,000 characters, parser
fixtures, completeness-gate and loader rejections, and the version-isolation
scenarios.

## License

The code is MIT licensed (see [LICENSE](LICENSE)). Specification text in the bundled
indexes and in every excerpt the server returns is © OASIS Open. See
[NOTICE](NOTICE), which reproduces the OASIS copyright and IPR notices of both
publications and explains that the indexes are transformed retrieval aids, not
official or modified publications.
