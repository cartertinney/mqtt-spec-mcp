# Agent benchmark results: Claude Sonnet 5, 2026-10-19

- **Runner:** GitHub Copilot SDK (`mqtt-spec-bench-copilot`), model `claude-sonnet-5`,
  one fresh session per run, 6 sessions at a time, 1 epoch, seed 20261016.
- **Corpus:** 200 cases (120 MQTT 5.0, 60 MQTT 3.1.1, 20 comparisons); see
  [AUTHORING.md](../AUTHORING.md).
- **Scoring:** scorer 1.3.1, rescored offline with the final answer keys
  (`mqtt-spec-bench-report --rescore`).
- **Server:** the `mcp` condition ran against commit 941005a (passage snippets,
  640-character later snippets, current guidance). An earlier `mcp` run against the
  previous server is compared below.
- **Usage:** 800 runs plus 200 `mcp` re-runs and 12 re-runs of three comparison
  questions whose wording was corrected; 2,005 premium requests. No run errored.

## All cases

| condition | quality | pass rate | tool calls | model calls | tokens/run | latency s (mean / p95) | premium requests/run |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `full_spec` | 0.996 | 0.970 | 0.00 | 1.00 | 90,445 | 7.27 / 12.39 | 1.00 |
| `grep` | 0.976 | 0.885 | 2.27 | 2.98 | 9,180 | 9.44 / 15.52 | 2.98 |
| `mcp` | 0.968 | 0.860 | 1.61 | 2.37 | 10,644 | 8.96 / 14.78 | 2.37 |
| `none` | 0.879 | 0.600 | 0.00 | 1.00 | 1,000 | 8.26 / 16.72 | 1.00 |

## By version

| version | condition | quality | pass rate | tokens/run | latency s (mean) |
| --- | --- | ---: | ---: | ---: | ---: |
| 5.0 (120) | `full_spec` | 0.997 | 0.983 | 99,909 | 6.86 |
| 5.0 (120) | `grep` | 0.978 | 0.917 | 8,163 | 8.65 |
| 5.0 (120) | `mcp` | 0.967 | 0.875 | 10,692 | 8.48 |
| 5.0 (120) | `none` | 0.883 | 0.625 | 967 | 8.06 |
| 3.1.1 (60) | `full_spec` | 0.997 | 0.967 | 51,388 | 6.58 |
| 3.1.1 (60) | `grep` | 0.980 | 0.900 | 9,247 | 9.70 |
| 3.1.1 (60) | `mcp` | 0.975 | 0.883 | 10,022 | 8.79 |
| 3.1.1 (60) | `none` | 0.865 | 0.567 | 1,021 | 8.24 |
| both (20) | `full_spec` | 0.982 | 0.900 | 150,836 | 11.78 |
| both (20) | `grep` | 0.944 | 0.650 | 15,077 | 13.41 |
| both (20) | `mcp` | 0.952 | 0.700 | 12,221 | 12.31 |
| both (20) | `none` | 0.901 | 0.550 | 1,143 | 9.51 |

## `mcp` against `full_spec` (paired, bootstrap 95% CI)

| version | pairs | quality delta | token reduction | speedup |
| --- | ---: | --- | --- | --- |
| all | 200 | -0.028 [-0.043, -0.014] | 0.882 [0.873, 0.891] | 0.81 [0.78, 0.84] |
| 3.1.1 | 60 | -0.022 [-0.047, -0.003] | 0.805 [0.784, 0.823] | 0.75 [0.71, 0.80] |
| 5.0 | 120 | -0.030 [-0.052, -0.013] | 0.893 [0.882, 0.902] | 0.81 [0.77, 0.85] |
| both | 20 | -0.030 [-0.073, 0.014] | 0.919 [0.903, 0.931] | 0.96 [0.88, 1.04] |

Release gates (`--gate-quality-regression 0.02 --gate-token-reduction 0.50
--gate-speedup 1.20`): token reduction passes; quality and speed fail.

## Findings

- **Tokens:** `mcp` reads about 10,600 tokens per question against about 90,400 with
  the whole specification in the prompt: 80% less for 3.1.1, 89% less for 5.0 and
  92% less for comparisons, which need both specifications in the prompt.
- **Quality:** `mcp` is close to `full_spec` but measurably below it: 2.8 points of
  quality and 11 points of pass rate. In the 26 cases where `mcp` failed and
  `full_spec` passed, 14 missing facts and most of the 17 missing citation groups
  were in text the model never read, usually a second relevant section (for
  example 3.3.4 PUBLISH Actions for Topic Alias errors, 3.1.2.11.9 for enhanced
  authentication). Only 2 missing facts were in evidence the model had read, and
  one answer used no tool.
- **Speed and premium requests:** `full_spec` answers in one model call and, with a
  constant system prompt, 90% of its input tokens were cache reads, so it was the
  fastest condition. `mcp` needs a tool round trip (2.37 model calls per question),
  so it takes about 23% longer (speedup 0.81) and, where billing is per model call
  (Copilot premium requests), costs 2.4 times as many requests per question despite
  the token reduction. An uncached first question with the whole specification costs
  far more input processing than these numbers show.
- **Generic grep is as good:** a plain line search and reader over the same text
  scored 0.976 with slightly fewer tokens than `mcp`, at the cost of more calls.
  The MCP server's advantages over it are version isolation, exact catalogs and
  section-aware citations, not raw accuracy on these questions.
- **Without reference material** the model still scores 0.879 (60% pass): it knows
  MQTT well, and most of its failures are missing details and section citations.

## Server change between the two `mcp` runs

The first `mcp` run used single-window snippets, 320-character later snippets and
the previous guidance. Rescored with the same final keys:

| `mcp` run | quality | pass rate | tokens/run | tool calls | model calls | latency s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 0.966 | 0.860 | 11,435 | 1.90 | 2.56 | 9.26 |
| after | 0.968 | 0.860 | 10,644 | 1.61 | 2.37 | 8.96 |

Quality did not change measurably (paired delta +0.002, 95% CI [-0.016, +0.018]);
the new snippets carry more of the needed facts per search, and the model made
fewer calls, so each answer took 7% fewer tokens and 3% less time.

## Scoring checks

- Every case's reference answer scores 1.0; a non-answer scores 0.0 on every case.
- Scored against 1,000 reference answers of other cases of the same version, only
  3 passed (all near-duplicate topics) and the mean quality was 0.04.
- In the first report, before the review-driven key fixes, the same stored answers
  scored 0.921 (`full_spec`), 0.863 (`mcp`, first run) and 0.754 (`none`): most
  early failures were keys that rejected correct phrasing. Review and fixes are
  described in [AUTHORING.md](../AUTHORING.md).
