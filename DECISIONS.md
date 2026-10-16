# Decisions log

Design choices made where the requirements were silent or left a choice open.
Each entry states the decision and, briefly, why.

## Clean room

- Everything here was written from the requirements document, the public MCP
  specification and SDK documentation (including the documentation shipped
  inside the `mcp`, `inspect-ai` and `github-copilot-sdk` packages), and the two
  public OASIS MQTT publications. The original `cartertinney/mqtt-spec-mcp`
  repository, its forks, data files, benchmark cases and results were not
  viewed, fetched or searched.

## Runtime and protocol

- **Python + uv**, official MCP Python SDK 2.x low-level `Server`, stdio only.
  The low-level API gives exact control over tool schemas (no `default` on
  `version`), annotations and single-text-content results.
- **Argument validation** uses strict Pydantic models with unknown arguments
  rejected (`extra="forbid"`), so an inapplicable argument is never silently
  ignored, and `5`, `true`, `"5"` etc. never coerce into a version.
- Nullable parameters are declared as `{"type": ["string", "null"]}`.
- The server works with both the legacy `initialize` handshake and the
  2026-07-28 per-request protocol era; the SDK negotiates this.
- `MqttSpecService` is the transport-independent dispatcher. The test hook is
  `MqttSpecService(["5.0"])` / `create_server(["5.0"])`.

## Source pinning and canonicalization

Canonicalization (`scripts/indexgen/canonical.py`) operates on raw bytes:

1. Cloudflare `data-cfemail` spans/links are decoded (first byte is the XOR key)
   back to plain text or `mailto:` links.
2. `<script>` elements containing `cdn-cgi`, `cloudflare`, `__CF$cv$params` or
   `cf-beacon` are removed (OASIS responses sometimes carry a Cloudflare
   Insights beacon); other scripts are kept.
3. All whitespace before `</body>` becomes a single CRLF.

SHA-256 of the canonical bytes is pinned per version in
`src/mqtt_spec_mcp/versions.py`. Two fetches that differed in raw bytes (with and
without the beacon) produced identical canonical hashes. These pins differ from
the original project's, because the canonicalization was designed independently:

| Version | Canonical SHA-256 |
| --- | --- |
| 3.1.1 | `93167b6b27ec9b190e7f258fea4a591903480f7803af23b7b7fa31e99bce96c9` |
| 5.0 | `623cd5b41e73c7518cb9ccce235e45b897af9e5a891d3534309f85bd118a680c` |

`--allow-source-mismatch` writes the actual hash into the index metadata, so the
loader refuses that index until the pin is updated deliberately.

## Parsing

- Body parsing starts at the first numbered heading `1 …` and stops at the first
  `AppendixHeading1` paragraph, in **both** versions (not only 3.1.1), so appendix
  text is never attributed to section 7.x. Appendix blocks are captured
  separately and used only for label reconciliation (below).
- Word `MsoCommentText` paragraphs are body text in these publications (3.1.1
  section 3.1.2.4 carries `MQTT-3.1.2-4` and `MQTT-3.1.2-5` in them), so they are
  kept. Table-of-contents, header and footer classes are skipped.
- Word list markers are detected from the `font:7.0pt` spacer span. Bullet glyphs
  are stripped; ordinal markers (`1.`, `a)`, `(i)`) are kept in the text because
  the numbering carries meaning.
- 3.1.1 headings that are not numbered (`Table 2.1 - …`, `Figure 3.1 - …`) become
  paragraphs, so the shared "caption directly before a table" rule applies.
- Cell text joins block-level children with spaces; rowspan/colspan values are
  copied into every covered grid position; empty rows are dropped.
- Glossary terms: a paragraph (not list item) that starts with a short name of at
  most five words, beginning with a capital letter, followed by `:` starts a term;
  following blocks belong to it until the next term. This handles both the inline
  (`Name: definition`) and heading (`Name:` then paragraphs) forms, which in fact
  appear mixed in both publications.
- Fixed-header flags: a 4-bit string when all four bit cells are `0`/`1`;
  otherwise `"<flags column> (Bit 3: …, Bit 2: …, Bit 1: …, Bit 0: …)"` keeping
  the printed cells (3.1.1 prints footnote markers such as `DUP1`).
- Property sections: `2.2.2.2` plus every chapter-3 section whose title equals the
  property name after normalization, tolerating singular/plural spelling (the
  publication titles 3.2.2.3.12 "Subscription Identifiers Available").
- Packet sections are the chapter-3 level-2 sections whose title starts with the
  packet name (`3.1 CONNECT – …`).

### MQTT 5.0 requirement reconciliation

The 5.0 body prints 250 distinct bracketed labels, while the completeness gate
requires 252. Appendix B (the publication's own list of normative statements)
names two labels the body does not print recognizably: `MQTT-3.2.2-8` (printed as
`T-3.2.2-8]` in 3.2.2.2) and `MQTT-4.2.0-1` (printed as `MQTT-4.2-1` in 4.2). For
each Appendix B label missing from the body, the generator finds the body block
containing the Appendix B statement text and indexes the label there; it fails if
no such block exists. This is a no-op for 3.1.1, whose Appendix B labels all
appear in the body.

## Search

- BM25F over three fields: title (weight 5, b=0.3), ancestor titles ("path",
  weight 1, tf capped at 1) and body (b=0.5), k1=1.6, one saturation per term.
  This rewards covering several query terms over repeating one title word.
- Tokens are lowercase alphanumeric runs after NFKC/dash normalization, with a
  light plural fold (`subscriptions` → `subscription`). A small English stopword
  list is dropped from queries unless the query is only stopwords.
- Boosts: section id = 1000; requirement label in its home section = 600, in
  another section that lists it = 400; title equal = 300; title contains the
  query = 100; body contains the whole phrase = 10; each adjacent query-term pair
  in the title = 6, in the body = 2; +0.5 per normative label (max 4).
  Requirement-label and section-id queries skip BM25 (their digits are noise).
- Snippets: 1,600 characters for the first result, 320 for others, 700 for
  `parent_context`; all limits include the `...` markers. The window starts at
  the beginning of the block containing the match when that is close, otherwise
  shortly before the match, and is cut at word boundaries.
- For 3.1.1 label queries the snippet is a window on the requirement's indexed
  excerpt (as required); 5.0 label queries use the normal phrase window, which
  locates the printed label directly.

## Section resolution

- `"<id> <title>"` accepts the title when it equals the section's normalized
  title, or either is a word-boundary prefix of the other (`3.1 CONNECT`
  resolves to `3.1 CONNECT – Connection Request`); anything else fails with
  `title does not match`.
- Conflicting-version guard: `MQTT 5`, `MQTT 5.0`, `MQTT5.0`, `MQTT v5.0`,
  `MQTT Version 5.0` mean 5.0; `MQTT 3.1.1` means 3.1.1; `MQTT 3.1` (unsupported)
  is rejected for both; a bare `MQTT 3` is treated as 3.x (allowed for 3.1.1).
  Requirement labels (`MQTT-3.1.2-4`) never trigger the guard.

## Get and cursors

- Fragments are at most 600 characters (less when `max_chars` leaves less room).
  Room is reserved for the "(continued)" header, banner and a footer containing
  the longest possible cursor (position 10^7), so every page fits.
- Subsection headings use `#` × (1 + level difference), capped at 6.
- A section without its own blocks renders a placeholder that points to
  `include_subsections` or `mqtt_spec_outline`.
- Cursor = URL-safe base64 (no padding) of compact JSON
  `{"f":1,"h":<full source sha256>,"i":<bool>,"m":<max_chars>,"p":<position>,
  "s":<section>,"v":<version>}`. Unsigned: cursors carry no secrets and the server
  validates every field.

## Lookup

- Return-code and packet `code` fields are hex strings (`0x00`).
- A single property result always has `details`; it is empty without a packet
  filter.
- `return_code` accepts `packet=CONNACK`/`SUBACK` only (case-insensitive name).

## Benchmark

- Case schema additions beyond the required fields: each fact and forbidden claim
  names the `section` that grounds it, and each case has a `reference_answer`.
  The validator requires every fact to have one regex alternative matching its
  section's text, every case's reference answer to pass the scorer within its
  word limit, no 8-word run of a prompt or search query to be copied from the
  spec, and lookups/queries not to reuse acceptance-test scenarios.
- Retrieval metrics: Recall@k = mean(|relevant ∩ top-k| / min(|relevant|, k));
  hit rate = any relevant section in the top 5; MRR on the first relevant hit.
- Scorer negation (scorer 1.1.0): an affirmed fact is not credited if a negation
  cue appears in the 48 characters before the match within the same clause; a
  negated fact requires a cue there or inside the match; a forbidden claim is a
  violation only when no cue appears before it or inside it. (1.0.0 ignored cues
  inside forbidden matches, which flagged correct answers such as "should not
  retransmit"; found by the answer-key review.) Section citations ignore numbers
  directly preceded by `MQTT`, `v` or `version`; citing a descendant of a cited
  section satisfies the group.
- All four agent conditions are implemented as in-process tools with identical
  loop and limits (12 tool calls). The `mcp` condition exposes this server's real
  tool schemas and instructions, served by a 5.0-only service ("pinned"), so any
  3.1.1 call fails rather than leaking the other version.
- Copilot SDK runner: `CopilotClient(mode="empty")`, built-in tools disabled via
  `available_tools` (custom tools only, or none), system message in `replace`
  mode, infinite sessions off, one fresh session per run.
- Inspect AI runner: a per-sample `react` agent without the submit tool, so the
  loop ends when the model answers, matching the Copilot loop. Conditions are
  separate tasks; the case order is seeded via dataset shuffling.
- The benchmark covers MQTT 5.0 only for v1; extending it to 3.1.1 is deferred.
- Not published to PyPI for v1.
