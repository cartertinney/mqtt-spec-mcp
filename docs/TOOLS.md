# Tool reference

## Version scope and results

Every tool requires `version`, exactly `"3.1.1"` or `"5.0"`. There is no default;
`"5"`, `"v3.1.1"`, `"latest"`, and numeric values are rejected. Each call queries
only that version. To compare versions, call separately and cite each explicitly.

Outline and lookup results are JSON with `specification`, `version`, `scope`, and
`source` fields. Search and get results are Markdown with version scope and source
links. The OASIS text, not the agent's interpretation, is the authority.

The advertised tool schemas contain the parameters agents normally need.
Additional accepted parameters are documented below.

Errors use MCP tool errors (`isError: true`). When a valid version was supplied,
the message starts with `MQTT <version> only (<source URL>):`, explains the
problem and remedy, and ends with `No other version was queried.` Free-text
arguments naming the other version are rejected rather than silently redirected.

## `mqtt_spec_outline`

| Parameter | Meaning |
| --- | --- |
| `version` | Required version. |
| `parent` | Optional section id or unambiguous title. |
| `depth` | Accepted range 1-5; default 1. |

Returns ids and titles without body text.

```json
{"version": "5.0", "parent": "3"}
```

This lists packet sections `3.1` (CONNECT) through `3.15` (AUTH). MQTT 3.1.1 has
14 packet sections instead.

## `mqtt_spec_search`

| Parameter | Meaning |
| --- | --- |
| `version` | Required version. |
| `query` | Required key terms, section id, or requirement label. |
| `chapter` | Optional restriction to a section's subtree. |
| `limit` | Accepted range 1-8 sections per query part. Suppresses related requirements when supplied. |
| `normative_only` | Optional restriction to normative content. |

```json
{"version": "5.0", "query": "Topic Alias 0 or above Topic Alias Maximum"}
{"version": "5.0", "query": "Receive Maximum send quota; Receive Maximum default; QoS 0 Receive Maximum"}
```

Everyday spellings are matched to specification terminology, including `keepalive`
to Keep Alive, `LWT`/`last will` to Will, `broker` to Server, `QoS0` to QoS 0,
`client id` to ClientID, and `kick` to disconnect.

### Evidence packs

A single-part query returns roughly 5,500 characters of excerpts from up to eight
ranked sections, plus up to 700 characters of related labelled requirements from
other matching sections. Each section has a `## <id> <title>` heading.

Sentences carrying the most query information are selected first, with neighbouring
context. Normative sentences count double. List excerpts include their lead-in,
and short lists are kept whole. Spare room goes first to the best-ranked section.
Verbatim duplicate sentences are shown once.

`...` marks cuts. A heading ends with `(excerpt: N of M characters)` when less
than 95% of its section is shown. The agent guidance treats unmarked sections as
complete; the marker is not a guarantee that every exception is present.

For multipart questions, separate groups of key terms with `;`. Up to four parts
are ranked independently; extra parts are joined to the fourth. Parts share a
9,000-character excerpt budget, with at least 2,500 allocated per part; duplicate
sections and sentences are merged. Including headings and guidance, a pack can
reach about 11,000 characters.

A requirement-label query shows the requirement in its home section and sections
listing it. A section-id query starts at the beginning of that section.

### When to fetch more

State the applicable rules, normative strengths, and conditions, with citations.
Before claiming the specification is silent, leaves something optional, or has no
exception, fetch the deciding section when it is excerpted or absent. The server
recommends at most one fetch and one repeat search per version; a repeat search
should address an uncovered part of the question. Use outline only to browse.

## `mqtt_spec_get`

| Parameter | Meaning |
| --- | --- |
| `version` | Required version. |
| `section` | Required id, id plus matching title, or unambiguous title. |
| `cursor` | Continuation cursor from a previous page. |
| `include_subsections` | Accepted option to include descendants. A section with no text of its own always includes them. |
| `max_chars` | Accepted range 1,000-12,000; default 4,000. |

```json
{"version": "3.1.1", "section": "3.1.2.4"}
```

Pages include the section title, version scope, source, and a truncation footer.
Tables are Markdown tables and list items use bullets. A merged table cell is
written once with the columns it spans, for example
`| byte 1 | String length MSB (bits 7–0) |`. Pages never exceed `max_chars`.

A section with no text of its own, such as MQTT 5.0 section 4.13, returns its
subsections rather than an empty page.

When `Truncated: yes`, repeat the same arguments with the returned `Next cursor`.
A cursor is bound to the version, section, `include_subsections`, and `max_chars`
that issued it. It cannot be reused across versions or with changed page options.

Section titles are version-specific: `3.1.2.4 Clean Start` is rejected for MQTT
3.1.1 because that section is Clean Session there.

## `mqtt_spec_lookup`

Parameters: required `version`, `kind`, and `query`; optional `packet` filter.

| Kind | MQTT 3.1.1 | MQTT 5.0 | Query |
| --- | --- | --- | --- |
| `packet` | 14 packets | 15 packets | Name or value. |
| `property` | Unavailable | 27 properties | Identifier, name, or fragment. |
| `reason_code` | Unavailable | 45 entries | Value or name; `packet` filters. |
| `return_code` | CONNACK and SUBACK | Unavailable | Value or name; packet name lists its codes. |
| `requirement` | 141 labels | 252 labels | Requirement label. |
| `term` | 9 glossary terms | 15 glossary terms | Name or fragment. |

```json
{"version": "5.0", "kind": "property", "query": "0x11", "packet": "CONNECT"}
{"version": "3.1.1", "kind": "return_code", "query": "0x80", "packet": "SUBACK"}
```

Results include evidence, not just names:

- Packets have summaries, fixed-header prose, fixed Remaining Length where
  applicable, and the start of Actions or Response sections.
- Properties include excerpts from their defining sections: the selected packet's
  section, or each defining section when there are at most three.
- Reason codes (up to three matches) and return codes include packet-specific
  table meanings and senders where named by the source.

MQTT 3.1.1 return codes include CONNACK 0-5 and SUBACK 0/1/2/0x80.
MQTT 5.0 has Clean Start, Session Expiry, properties, and reason codes; MQTT 3.1.1
has Clean Session and return codes. Do not transfer their section numbers, labels,
or meanings between versions.
