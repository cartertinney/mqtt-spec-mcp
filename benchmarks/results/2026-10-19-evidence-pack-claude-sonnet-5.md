# Agent benchmark results: evidence-pack search, Claude Sonnet 5, 2026-10-19

- **Runner:** GitHub Copilot SDK (`mqtt-spec-bench-copilot`), model `claude-sonnet-5`,
  one fresh session per run, seed 20261016.
- **Corpus:** 200 cases (120 MQTT 5.0, 60 MQTT 3.1.1, 20 comparisons). Answer keys
  after three blind checks against stored answers; see [AUTHORING.md](../AUTHORING.md).
- **Scoring:** scorer 1.3.2; every stored answer was rescored offline with the final
  keys (`mqtt-spec-bench-report --rescore`).
- **Usage:** 2,600 runs in six passes, 6,213 premium requests, no errored runs.
- **Design:** every pass ran `mcp` and `grep` side by side (the first also
  `full_spec`), because the same model scores measurably differently a few hours
  apart. Only conditions run together are compared.

This follows [2026-10-19-claude-sonnet-5.md](2026-10-19-claude-sonnet-5.md), where
the previous search (JSON results with snippets, server v3) was as accurate as grep
but used 8% more raw tokens.

## Current server (v8, commit 32914d9) against grep

Two epochs run back to back, 400 paired runs. None of these answers was seen by any
key check, so the keys were not fitted to them.

| condition | quality | pass rate | tokens/question | cost-weighted tokens | model calls | latency s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `mcp` | 0.977 | 0.880 | 7,816 | 7,863 | 2.11 | 8.34 |
| `grep` | 0.971 | 0.865 | 8,667 | 7,619 | 2.85 | 9.23 |

Cost-weighted tokens count cache reads at 0.1, cache writes at 1.25 and output at 5
times an uncached input token. Paired by case, bootstrap 95% CI resampling cases:

| `mcp` vs `grep` | estimate | 95% CI |
| --- | ---: | --- |
| quality delta (points) | +0.6 | -0.4 to +1.8 |
| pass-rate delta (points) | +1.5 | -3.2 to +6.5 |
| token reduction | 9.8% | 2.5% to 16.2% |
| cost-weighted token reduction | -3.2% | -9.3% to +2.3% |
| speedup | 1.11 | 1.08 to 1.14 |

| version | `mcp` quality | `grep` quality | `mcp` tokens | `grep` tokens |
| --- | ---: | ---: | ---: | ---: |
| 5.0 (240) | 0.977 | 0.976 | 7,523 | 8,234 |
| 3.1.1 (120) | 0.984 | 0.971 | 7,827 | 8,555 |
| both (40) | 0.960 | 0.945 | 9,538 | 11,605 |

| category | `mcp` quality | `grep` quality | `mcp` tokens | `grep` tokens |
| --- | ---: | ---: | ---: | ---: |
| 5.0 lookup (70) | 0.993 | 0.976 | 5,668 | 5,242 |
| 5.0 normative (60) | 0.978 | 0.983 | 7,374 | 8,770 |
| 5.0 applied (50) | 0.983 | 0.969 | 7,699 | 9,038 |
| 5.0 synthesis (30) | 0.959 | 0.972 | 9,635 | 11,595 |
| 5.0 edge (30) | 0.947 | 0.972 | 9,744 | 9,440 |
| 3.1.1 lookup (36) | 0.989 | 0.978 | 6,496 | 4,938 |
| 3.1.1 normative (30) | 0.981 | 0.965 | 6,839 | 8,819 |
| 3.1.1 applied (24) | 0.975 | 0.942 | 8,240 | 10,490 |
| 3.1.1 synthesis (16) | 0.982 | 0.983 | 10,815 | 13,553 |
| 3.1.1 edge (14) | 0.993 | 1.000 | 9,247 | 8,263 |
| comparison (40) | 0.960 | 0.945 | 9,538 | 11,605 |

Category differences move from pass to pass by more than they differ here; read
them as noise unless they recur below.

## All evidence-pack passes

Same keys throughout. Answers from v4 to v7 were used in the blind key checks; v8
was not.

| pass | `mcp` server | `mcp` quality | `grep` quality | quality delta [95% CI] | token reduction [95% CI] | cost-weighted reduction |
| --- | --- | ---: | ---: | --- | --- | ---: |
| 1 | v4 db37866 | 0.978 | 0.978 | +0.0 [-1.3, +1.4] | 9.0% [0.1, 17.3] | 0.8% |
| 2 | v5 66a0177 | 0.972 | 0.978 | -0.6 [-1.9, +0.6] | 16.7% [6.1, 26.0] | 7.0% |
| 3 | v6 65b68a9 | 0.975 | 0.985 | -1.1 [-2.5, +0.2] | 11.2% [0.4, 21.0] | 4.3% |
| 4 | v7 64c2fa0 | 0.979 | 0.987 | -0.8 [-2.3, +0.5] | 9.4% [-0.2, 17.8] | 2.8% |
| 5 | v8 32914d9 | 0.975 | 0.977 | -0.2 [-1.6, +1.3] | 7.9% [-1.3, 15.5] | -5.0% |
| 6 | v8 32914d9 | 0.980 | 0.965 | +1.5 [+0.1, +2.8] | 11.7% [3.8, 18.4] | -1.4% |
| all | | 0.977 | 0.978 | -0.2 [-0.9, +0.5] | 11.1% [3.8, 17.6] | 1.5% |

In pass 1, `full_spec` (the whole specification in the prompt) scored 0.994 (97.5%
pass) with 90,424 tokens per question: `mcp` used 91% fewer tokens and scored 1.6
points lower.

## Findings

- **Tokens.** `mcp` used about 10% fewer raw tokens than `grep` in every pass (11.1%
  pooled, CI 3.8% to 17.6%). It answers from fewer model calls (2.1 against 2.9),
  each carrying a larger tool result. Under cache pricing the two are about even:
  a search result is written to the prompt cache once at 1.25, while grep's extra
  calls mostly re-read its smaller prefix at 0.1. The current server's related
  requirements made results about 150 tokens larger, which moved cost-weighted
  tokens from a few percent below grep to a few percent above (within noise).
- **Accuracy.** Tied. Across 1,200 pairs `mcp` is 0.2 points below `grep` (CI -0.9
  to +0.5); the current server is 0.6 points above it on answers no key check had
  seen (CI -0.4 to +1.8). Relative to the `grep` runs beside them, the current
  server did better than v4 to v7 (+0.6 against an average of -0.6), but a
  difference of about one point needs several more passes to resolve, and both
  conditions sit near the ceiling that `full_spec` sets.
- **Speed.** `mcp` was about 11% faster in every pass.
- **Where each loses.** Over 1,200 runs each, both missed 53 facts that their tools
  never returned. `mcp` left out more facts that were in its results (54 against
  30); `grep` cited less precisely (citation recall 0.968 against 0.976). Lookup
  questions cost `mcp` more tokens than `grep` (5,668 against 5,242 for 5.0): with a
  short answer the larger fixed prefix of four tool definitions and the server
  instructions dominates.
- **The model drifts.** The unchanged `grep` condition scored between 0.965 and
  0.987 across the six passes on the same keys.
- **Measurement.** Each new set of answers brought phrasings the regular-expression
  keys rejected. The three key checks were blind to condition and moved both
  conditions about equally on answers they had not seen (+0.38 `grep`, +0.31
  `mcp` for the current server's runs).

## Server versions

| version | commit | changes |
| --- | --- | --- |
| v4 | db37866 | Search returns an evidence pack: the most informative sentences across the eight best-ranked sections, 5,500 characters, Markdown; lean tool schemas and instructions |
| v5 | 66a0177 | The next step asks for each relevant rule with its MUST/SHOULD/MAY; shorter scope sentence |
| v6 | 65b68a9 | Normative sentences count double and the best section fills first; lookups carry spec text (packet summary and fixed header, property excerpts, per-packet code meanings) |
| v7 | 64c2fa0 | `return_code` without a packet filter; one search of the other version allowed for comparisons; packet Actions in lookups; consult the tools before answering |
| v8 | 32914d9 | Related requirements from sections ranked below the pack; leaner advertised schemas |
