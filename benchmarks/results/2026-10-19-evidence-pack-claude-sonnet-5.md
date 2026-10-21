# Agent benchmark results: evidence-pack search, Claude Sonnet 5, 2026-10-19

> Historical development evaluation, not held-out validation. The cases are
> public, but the raw runs and the agent harness are private. Commands below
> refer to tooling that has since moved; see the [benchmarks guide](../README.md).

- **Runner:** GitHub Copilot SDK (`mqtt-spec-bench-copilot`), model `claude-sonnet-5`,
  one fresh session per run, seed 20261016.
- **Corpus:** 200 cases (120 MQTT 5.0, 60 MQTT 3.1.1, 20 comparisons). Answer keys
  after three blind checks against stored answers; see [AUTHORING.md](../AUTHORING.md).
- **Scoring:** scorer 1.3.2; every stored answer was rescored offline with the final
  keys (`mqtt-spec-bench-report --rescore`).
- **Usage:** 3,204 runs in eight passes, 7,746 premium requests, no errored runs.
- **Design:** every pass ran `mcp` and `grep` side by side (the first also
  `full_spec`), because the same model scores measurably differently a few hours
  apart. Only conditions run together are compared.

This follows [2026-10-19-claude-sonnet-5.md](2026-10-19-claude-sonnet-5.md), where
the previous search (JSON results with snippets, server v3) was as accurate as grep
but used 8% more raw tokens.

## Current server (v9, commit 6448cb8): multi-part search

A query can carry one group of key terms per part of a question, separated by `;`,
and is answered with one pack (see the README). Run as two same-time comparisons
with `grep`: the 102 hard cases (synthesis, applied, edge, comparison) in two epochs,
then the 98 lookup and normative cases in one. No key check had seen these answers.
Each case is averaged over its runs, then over the 200 cases:

| condition | quality | pass rate | tokens/question | cost-weighted tokens | model calls | latency s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `mcp` | 0.979 | 0.892 | 7,730 | 7,752 | 2.06 | 8.08 |
| `grep` | 0.971 | 0.863 | 9,090 | 7,813 | 2.94 | 9.51 |

| `mcp` vs `grep` (bootstrap over cases) | estimate | 95% CI |
| --- | ---: | --- |
| quality delta (points) | +0.8 | -0.4 to +2.0 |
| pass-rate delta (points) | +3.0 | -2.2 to +8.0 |
| token reduction | 15.0% | 7.4% to 22.1% |
| cost-weighted token reduction | 0.8% | -4.9% to +6.1% |
| speedup | 1.18 | 1.14 to 1.21 |

Hard cases (204 pairs), against v8 on the same cases in its own same-time pass:

| category | `mcp` quality | `grep` quality | delta (95% CI) | v8 delta | `mcp` tokens | `grep` tokens |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| all hard | 0.971 | 0.960 | +1.05 [-0.68, +2.67] | +0.39 | 8,790 | 11,015 |
| synthesis | 0.955 | 0.958 | -0.25 [-3.52, +2.98] | -0.90 | 9,418 | 12,904 |
| applied | 0.979 | 0.977 | +0.17 [-2.26, +2.41] | +1.97 | 7,728 | 10,461 |
| edge | 0.978 | 0.980 | -0.15 [-3.18, +2.73] | -1.97 | 8,691 | 9,554 |
| comparison | 0.965 | 0.910 | +5.48 [+1.05, +10.23] | +1.55 | 10,141 | 11,472 |

On hard cases `mcp` used 20% fewer tokens (CI 11.5% to 27.3%; v8 13%) and its pass
rate was 5.9 points higher (CI -1.5 to +13.7; v8 -0.5). The model split 87 of 255
searches into parts. The gain over v8 is not concentrated where it did: relative to
the `grep` runs beside them, cases with a multi-part search moved from +0.74 to
+0.92 and the others from +0.05 to +1.14, and `grep` had a weaker pass (0.960
against 0.965). Where it split, `mcp` needed fewer follow-up calls and used about
340 fewer tokens. On the lookup and normative cases (98 pairs) `mcp` scored 0.987
against 0.982 with 6.5% fewer tokens, the same as v8 (0.986, +0.9 points); it split
10 of 66 searches.

## Previous server (v8, commit 32914d9) against grep

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

- **Tokens.** `mcp` used fewer raw tokens than `grep` in every pass: about 10% in
  the six full passes (11.1% pooled, CI 3.8% to 17.6%) and 15% with multi-part
  search (20% on hard questions, where `grep` reads the most). It answers from
  fewer model calls (2.1 against 2.9), each carrying a larger tool result. Under
  cache pricing the two are about even: a search result is written to the prompt
  cache once at 1.25, while grep's extra calls mostly re-read its smaller prefix at
  0.1. v8's related requirements made results about 150 tokens larger, which
  moved cost-weighted tokens from a few percent below grep to a few percent above
  (within noise); with multi-part search they are even again.
- **Accuracy.** No significant difference in any full comparison. Across the six
  passes `mcp` is 0.2 points below `grep` (CI -0.9 to +0.5); v8 is 0.6 above it
  (CI -0.4 to +1.8) and v9 0.8 above it (CI -0.4 to +2.0), on answers no key check
  had seen. Relative to the `grep` runs beside them the later servers did better
  (v4 to v7 averaged -0.6), but a difference of about one point needs several more
  passes to resolve, and both conditions sit near the ceiling that `full_spec`
  sets. Comparison questions are the one category where `mcp` leads clearly (+5.5
  points with v9, CI +1.1 to +10.2).
- **Speed.** `mcp` was 9% to 18% faster in every pass.
- **Where each loses.** Over 1,200 runs each, both missed 53 facts that their tools
  never returned. For `mcp` 37 of those were in a section the pack showed but cut
  (usually a list item or a rule that does not repeat the query's words, which led
  to multi-part search); `grep` had mostly never looked at the section (29) or saw
  only its matching lines (16). `mcp` left out more facts that were in its results
  (54 against 30); `grep` cited less precisely (citation recall 0.968 against
  0.976). Lookup
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
| v9 | 6448cb8 | Multi-part queries (`;`-separated parts, each ranked and excerpted on its own budget, one merged pack); a list item brings its lead-in and short lists come whole |
| - | 87a78db | Everyday spellings matched in the specifications' terms (`keepalive`, `LWT`, `broker`, `QoS0`, `client id`, `kick`). No agent pass: retrieval Recall@1 0.80 to 0.81 and MRR 0.87 to 0.88 on the corpus queries; 26 of the 1,569 searches the model made in these passes contain such words |
| - | after 3681635 | 3.1.1 indexed from MQTT Version 3.1.1 Plus Errata 01 instead of the 2014 HTML. Only the Remaining Length pseudocode in section 2.2.3 differs; every pass above used the 2014 text |
