# Decisions log

Design choices behind the server, the index generator and the benchmark. Each entry
states the decision and, briefly, why.

## Runtime and protocol

- **Python + uv**, official MCP Python SDK 2.x low-level `Server`, stdio only.
  The low-level API gives exact control over tool schemas (no `default` on
  `version`), annotations and single-text-content results.
- **Argument validation** uses strict Pydantic models with unknown arguments
  rejected (`extra="forbid"`), so an inapplicable argument is never silently
  ignored, and `5`, `true`, `"5"` etc. never coerce into a version.
- Nullable parameters are declared as `{"type": ["string", "null"]}`. Optional
  arguments accept the spellings agents actually send: null, blank text, or the
  words "null"/"none"/"undefined" mean "not given" (both the Copilot and Claude
  harnesses sent `"limit": "null"` in testing); `"3"` or `3.0` is accepted for an
  integer and `"true"`/`"false"` for a boolean. A bare number is accepted for the
  free-text `query`/`section`. `version` and range limits stay strict.
- Argument errors carry the `MQTT <version> only (...)` prefix whenever the
  `version` argument itself is valid. Any unexpected exception is caught and
  returned the same way as an internal-error tool result, so no unscoped error
  reaches the client.
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
without the beacon) produced identical canonical hashes:

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
- Tokens are lowercase alphanumeric runs after NFKC/dash normalization, stemmed
  with the Porter (1980) algorithm (`src/mqtt_spec_mcp/stemmer.py`, checked against
  the paper's published examples), so "closes"/"closed"/"closing" match.
  Exceptions keep MQTT concepts apart: "connection(s)" is not reduced to
  "connect", which would match every CONNECT packet section title.
- Stopwords are removed before stemming: English function words plus question
  fillers ("happen", "explain", "mean", ...). "will" is not a stopword because
  Will Message, Will Flag and Will Delay are MQTT terms.
- Boosts: section id = 1000 (also "Section 3.1", "§3.1", "3.1."); requirement
  label in its home section = 600, in another section that lists it = 400; title
  equal = 300; title contains the query = 100; body contains the whole phrase =
  10; each adjacent pair of query terms (ignoring stopwords between them) in the
  title = 6, in the body = 2; a 0x hex identifier present = 8; +0.25 per
  normative label (max 4 labels); query terms named only by ancestor headings get
  0.5 × idf each, unsaturated ("3.9.3 Payload" sits under "SUBACK"). The own
  title is already weighted inside BM25F; a separate own-title bonus was tried
  and rejected because it ranked single-property sections above 4.10.1 for
  multi-concept request/response questions.
  Requirement-label and section-id queries skip BM25 (their digits are noise).
- These settings were chosen by measuring the 120-case retrieval benchmark, a
  separate set of 45 practitioner queries, and title self-retrieval together,
  taking values from the flat middle of the range rather than the single best.
- Snippets: 1,600 characters for the first result, 320 for others, 700 for
  `parent_context`; all limits include the `...` markers. The window centers on
  the first whole-word phrase match, else on the densest cluster of informative
  query terms, and is cut at word boundaries.
- Requirement-label queries always show the requirement: 3.1.1 quotes the indexed
  excerpt (as required); 5.0 windows the printed label, falling back to the
  excerpt for the two labels reconciled from Appendix B.
- `next_step` names `best_section`, allows another listed section whose snippet
  fits better, and keeps the required "Do not search again for this question."

## Section resolution

- Accepted reference forms: `3.1.2.4`, `3.1.2.4.`, `Section 3.1.2.4`, `§3.1.2.4`,
  `Chapter 4`, and `"<id> <title>"` with a space, `-`, `–` or `:` between them.
- `"<id> <title>"` accepts the title when its words equal the section title's
  words, or either is a whole-word prefix of the other (`3.1 CONNECT` resolves to
  `3.1 CONNECT – Connection Request`); anything else fails with `title does not
  match`. Titles compare word by word, ignoring punctuation, so 5.0's heading
  "Message Expiry Interval\`" (a stray backtick in the publication) resolves.
- Conflicting-version guard: `MQTT 5`, `MQTT 5.0`, `MQTT5.0`, `MQTT-5`, `MQTT v5.0`,
  `MQTT version: 5.0`, `MQTT Version 5.0` mean 5.0; `MQTT 3.1.1` means 3.1.1;
  `MQTT 3.1` (unsupported) is rejected for both; a bare `MQTT 3` is treated as 3.x
  (allowed for 3.1.1; the error for 5.0 suggests version='3.1.1'). Requirement
  labels (`MQTT-3.1.2-4`, `MQTT-3-8.3-4`) never trigger the guard, and neither does
  an exact reference to a section of the selected version whose title names a
  version (5.0's `1.8.1 MQTT v3.1.1`).

## Get and cursors

- Fragments are at most 600 characters and at most half of the page's content
  room. Pages hold whole fragments, so a long fragment after a short one would
  otherwise leave most of a small page empty. Room is reserved for the
  "(continued)" header, banner and a footer containing the longest possible
  cursor (position 10^7), so every page fits.
- Long paragraphs split at the last sentence end (`.`, `;`, `:`, `?`, `!`, `)`
  followed by a space) that leaves the piece at least half full, else at the last
  word boundary.
- Subsection headings use `#` × (1 + level difference), capped at 6.
- A section without its own blocks renders a placeholder: "its subsections
  follow" when they are included, otherwise a pointer to `include_subsections`
  or `mqtt_spec_outline`.
- Cursor (format 2) = URL-safe base64 (no padding) of
  `2,<version>,<first 12 hex of the source SHA-256>,<section>,<0|1>,<max_chars>,<position>`,
  about 50 characters. The 12-hex prefix distinguishes pinned publications while
  keeping cursors short enough for agents to copy exactly. Unsigned: cursors carry
  no secrets and the server validates every field. Cursors over 128 characters or
  in any other format are rejected as invalid.

## Lookup

- Return-code and packet `code` fields are hex strings (`0x00`).
- A single property result always has `details`; it is empty without a packet
  filter.
- `return_code` requires the packet filter to resolve to CONNACK or SUBACK (by
  name, number, or "connack packet").
- A `reason_code` query that is a packet name ("CONNECT") is answered with an error
  pointing to `kind=packet`, instead of substring-matching reason code names such
  as "Normal disconnection".
- Requirement labels match with or without brackets, the `MQTT-` prefix, or
  trailing punctuation (`3.1.2-4`, `[MQTT-3.1.2-4].`).
- Numeric queries are limited to 9 decimal / 8 hex digits; longer digit strings
  are ordinary misses.

## Benchmark

- Case schema additions beyond the required fields: each fact and forbidden claim
  names the `section` that grounds it, and each case has a `reference_answer`.
  The validator requires every fact to have one regex alternative matching its
  section's text, every case's reference answer to pass the scorer within its
  word limit, no 8-word run of a prompt or search query to be copied from the
  spec, and lookups/queries not to reuse unit-test scenarios.
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
