# Agent benchmark results: Claude Sonnet 5, 2026-10-19

> Historical development evaluation, not held-out validation. The cases are
> public, but the raw runs and the agent harness are private. Commands below
> refer to tooling that has since moved; see the [benchmarks guide](../README.md).

- **Runner:** GitHub Copilot SDK (`mqtt-spec-bench-copilot`), model `claude-sonnet-5`,
  one fresh session per run, seed 20261016.
- **Corpus:** 200 cases (120 MQTT 5.0, 60 MQTT 3.1.1, 20 comparisons); see
  [AUTHORING.md](../AUTHORING.md).
- **Scoring:** scorer 1.3.1 with the final answer keys; every stored answer was
  rescored offline (`mqtt-spec-bench-report --rescore`).
- **Usage:** 1,812 runs, 3,722 premium requests, no errored runs.

## Same-time comparison with the current server (evening pass)

`full_spec`, `grep` and `mcp` (server at commit 4f35851: four results per search,
query-focused passages, short tool text) were run side by side in the early
evening, 17:15-17:36 ET (epoch 1; `mqtt-spec-bench-report --rescore --epoch 1`).
`none` ran only in the midday pass and is shown from there.

| condition | quality | pass rate | tokens/question | cost-weighted tokens | model calls | latency s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `full_spec` | 0.979 | 0.910 | 90,426 | 21,076 | 1.00 | 7.08 |
| `grep` | 0.949 | 0.795 | 8,971 | 7,678 | 2.91 | 9.54 |
| `mcp` | 0.952 | 0.745 | 9,723 | 7,013 | 2.33 | 8.62 |
| `none` (midday) | 0.879 | 0.600 | 1,000 | 3,351 | 1.00 | 8.26 |

Cost-weighted tokens count cache reads at 0.1, cache writes at 1.25 and output at 5
times an uncached input token, the usual ratios of Anthropic's prices; most of each
`mcp` and `full_spec` call is a cached prefix.

| version | `mcp` quality | `grep` quality | `full_spec` quality | `mcp` tokens | `grep` tokens | `full_spec` tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 5.0 (120) | 0.953 | 0.952 | 0.983 | 9,418 | 7,904 | 99,879 |
| 3.1.1 (60) | 0.969 | 0.971 | 0.988 | 9,928 | 8,972 | 51,387 |
| both (20) | 0.891 | 0.865 | 0.926 | 10,938 | 15,370 | 150,821 |

Paired by case, bootstrap 95% CI:

| comparison | quality delta | token reduction | speedup |
| --- | --- | --- | --- |
| `mcp` vs `full_spec` | -0.027 [-0.042, -0.013] | 0.893 [0.884, 0.900] | 0.82 [0.79, 0.86] |
| `mcp` vs `grep` | +0.003 [-0.016, +0.022] | -0.084 [-0.210, +0.027] | 1.11 [1.07, 1.15] |

Release gates for `mcp` vs `full_spec` (quality regression at most 0.02, token
reduction at least 0.50, speedup at least 1.20): token reduction passes; quality and
speed fail.

## Findings

- **Against the whole specification in the prompt**, `mcp` uses 89% fewer tokens
  (67% fewer cost-weighted) and scores 2.7 points lower, about the same gap as in the
  midday comparison of the previous server (2.8). It takes about 22% longer: one
  model call with a cached 90,000-token prompt is faster than a tool round trip.
- **Against plain grep**, `mcp` is as accurate (+0.3 points, within noise; -0.8 at
  midday), 11% faster and makes fewer model calls (2.3 against 2.9). It uses
  about 8% more raw tokens but 9% fewer cost-weighted tokens. Its fixed prefix (four
  tool definitions and the required server instructions) is about 2.4 times grep's
  and is re-sent with every call; for comparison questions `mcp` uses 29% fewer
  tokens than grep.
- **Where `mcp` loses facts**, they are mostly in a relevant section the model never
  read: it usually answers from one search (57% of runs), and the workflow allows one
  further `get`. Facts it had read but left out were rare.
- **The model drifted during the day.** The unchanged `grep` condition scored 2.7
  points lower in the evening than at midday (95% CI 1.0 to 4.3) and
  `full_spec` 1.7 points lower; two `mcp` passes run side by side in the evening
  agreed within 0.2 points. Only runs made together are compared here.
- **Without reference material** the model scored 0.879 (60% pass) at midday:
  it knows MQTT well, and its failures are mostly missing details and citations.

## Server versions

Rescored with the same keys. Versions 1 and 2 ran around midday (12:20-13:25 ET),
version 3 in the early evening, so their quality differences include the drift
above; relative to the baselines run with them, `mcp` held its place.

| `mcp` server | changes | pass | quality | tokens/question | model calls |
| --- | --- | --- | ---: | ---: | ---: |
| v1 (f76a154) | single-window snippets, 3 results, 320-char later snippets | midday | 0.966 | 11,450 | 2.56 |
| v2 (941005a) | query-focused passages, 640-char later snippets, guidance | midday | 0.968 | 10,644 | 2.37 |
| v3 (4f35851) | 4 results by default, shorter tool text and instructions | evening | 0.954 / 0.952 | 9,781 / 9,723 | 2.34 / 2.33 |

In the midday pass `full_spec` scored 0.996, `grep` 0.976, `mcp` 0.966 (v1) and
0.968 (v2), and `none` 0.879.

## Scoring checks

- Every case's reference answer scores 1.0; a non-answer scores 0.0 on every case.
- Scored against 1,000 reference answers of other cases of the same version, only
  3 passed (all near-duplicate topics) and the mean quality was 0.04.
- In the first report, before the review-driven key fixes, the midday answers
  scored 0.921 (`full_spec`), 0.863 (`mcp`) and 0.754 (`none`): most early failures
  were keys that rejected correct phrasing. Review and fixes are described in
  [AUTHORING.md](../AUTHORING.md).
