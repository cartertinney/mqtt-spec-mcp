# mqtt-spec-mcp

An offline [Model Context Protocol](https://modelcontextprotocol.io) server for
searching and citing the OASIS **MQTT 3.1.1** and **MQTT 5.0** specifications.
Agents get focused source excerpts instead of loading a whole specification.

- Four read-only tools for search, section retrieval, outlines, and exact lookups.
- Bundled indexes: no network, database, embeddings, or LLM at query time.
- Explicit version selection on every call; no default or cross-version results.
- Bounded section pages and compact search evidence packs with source links.

The server retrieves evidence; the agent does the reasoning. OASIS publications
remain authoritative, and retrieved excerpts are not a guarantee of a correct
answer.

## Install and run

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10 or newer. The package is
not published to PyPI; run it from a checkout:

```bash
git clone https://github.com/cartertinney/mqtt-spec-mcp.git
cd mqtt-spec-mcp
uv sync --locked
uv run mqtt-spec-mcp
```

This starts a stdio MCP server, not an interactive command prompt.
`uv run python -m mqtt_spec_mcp` also works. No environment variables, configuration
files, or server flags are needed.

## Connect a client

For Claude Desktop, Claude Code, Cursor, and clients using `mcpServers`:

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

For VS Code, put this in `.vscode/mcp.json`:

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

Replace the absolute path with your checkout. This repository also includes
[VS Code](.vscode/mcp.json) and [project-scoped](.mcp.json) configurations for using
the server while working in the checkout.

After upgrading, restart the server and start a new conversation so the client
drops cached tool schemas.

## Tools

Every call requires `version`: exactly `"3.1.1"` or `"5.0"`.

| Tool | Use |
| --- | --- |
| `mqtt_spec_search` | Find excerpts by key terms, section id, or requirement label. |
| `mqtt_spec_get` | Read a section, with bounded pages and continuation cursors. |
| `mqtt_spec_outline` | Browse section ids and titles without body text. |
| `mqtt_spec_lookup` | Look up packets, properties, codes, requirements, or glossary terms. |

Example tool arguments:

```json
{"version": "5.0", "query": "Receive Maximum default; QoS 0 Receive Maximum"}
```

Use that with `mqtt_spec_search`. For an exact lookup with `mqtt_spec_lookup`:

```json
{"version": "3.1.1", "kind": "return_code", "query": "0x80", "packet": "SUBACK"}
```

The latter returns **Failure**. The same value in MQTT 5.0 is **Unspecified error**:
version-specific labels and codes must not be carried between versions.

### Guidance for agents

1. Ask which MQTT version the user means if they have not specified one.
2. Look up an exact entity, or search with the question's key terms. Separate
   parts of a question with `;`.
3. Answer from the evidence, preserving MUST/SHOULD/MAY and their conditions.
   Cite the version and section beside each claim.
4. Search excerpts can omit exceptions. Before claiming silence, optionality, or
   no exception, fetch the deciding section if it is excerpted or absent.
   The server recommends at most one fetch and one repeat search per version.
5. For comparisons, query each version separately. A miss in one says nothing
   about the other.

See the [tool reference](docs/TOOLS.md) for all parameters, pagination, result
formats, and errors.

## Sources and limitations

| Version | Indexed publication |
| --- | --- |
| 3.1.1 | [MQTT Version 3.1.1 Plus Errata 01](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/errata01/os/mqtt-v3.1.1-errata01-os-complete.html), 10 December 2015 |
| 5.0 | [MQTT Version 5.0](https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html), 7 March 2019 |

Indexes are generated from pinned source documents and validated at startup.
They are transformed retrieval aids, not official publications. MQTT 3.1
(pre-OASIS) is not supported, and this is not a broker or a conformance validator.

[Limitations and roadmap](docs/STATUS.md) describe known retrieval gaps.
[Benchmarks](benchmarks/README.md) covers the offline regression checks and
reports from historical agent evaluations. Those evaluations used a private
harness, corpus, and raw runs, so they cannot be reproduced from this repository.

## Development

See [development and release checks](docs/DEVELOPMENT.md) for testing, rebuilding
indexes, and checking the installed wheel. Benchmark checks are checkout-only and
are not shipped with the server.

## License

The code is [MIT licensed](LICENSE). Specification text in the indexes and returned
excerpts is copyright OASIS Open, not MIT licensed. [NOTICE](NOTICE) contains the
source attribution, transformation details, and OASIS notices.
