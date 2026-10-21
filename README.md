# mqtt-spec-mcp

<!-- mcp-name: io.github.cartertinney/mqtt-spec-mcp -->

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

Requires Python 3.10 or newer. The simplest way to run the server is with
[uv](https://docs.astral.sh/uv/):

```bash
uvx mqtt-spec-mcp
```

`uvx` installs the package from PyPI into a cached, isolated environment and
starts it. This starts a stdio MCP server, not an interactive command prompt;
MCP clients launch it for you, as shown below.

You can also install it with pipx or pip and run the `mqtt-spec-mcp` command:

```bash
pipx install mqtt-spec-mcp    # or: pip install mqtt-spec-mcp
mqtt-spec-mcp
```

The server needs no environment variables, configuration files, flags, or network
access.

## Connect a client

For Claude Desktop, Claude Code, Cursor, and clients using `mcpServers`:

```json
{
  "mcpServers": {
    "mqtt-spec": {
      "command": "uvx",
      "args": ["mqtt-spec-mcp"]
    }
  }
}
```

For VS Code, put this in `.vscode/mcp.json` or your user MCP configuration:

```json
{
  "servers": {
    "mqtt-spec": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mqtt-spec-mcp"]
    }
  }
}
```

If the client can't find `uvx`, which is common for desktop apps that don't use
your shell's `PATH`, give its absolute path (`which uvx` prints it).

`uvx` keeps using the version it has cached. To upgrade, run
`uvx mqtt-spec-mcp@latest` once. To pin a version, put `mqtt-spec-mcp@0.1.0` (or
another release) in `args`. After upgrading, restart the server and start a new
conversation so the client drops cached tool schemas.

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

See the [tool reference](https://github.com/cartertinney/mqtt-spec-mcp/blob/main/docs/TOOLS.md)
for all parameters, pagination, result formats, and errors.

## Sources and limitations

| Version | Indexed publication |
| --- | --- |
| 3.1.1 | [MQTT Version 3.1.1 Plus Errata 01](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/errata01/os/mqtt-v3.1.1-errata01-os-complete.html), 10 December 2015 |
| 5.0 | [MQTT Version 5.0](https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html), 7 March 2019 |

Indexes are generated from pinned source documents and validated at startup.
They are transformed retrieval aids, not official publications. MQTT 3.1
(pre-OASIS) is not supported, and this is not a broker or a conformance validator.

[Limitations and roadmap](https://github.com/cartertinney/mqtt-spec-mcp/blob/main/docs/STATUS.md)
describe known retrieval gaps.
[Benchmarks](https://github.com/cartertinney/mqtt-spec-mcp/blob/main/benchmarks/README.md)
covers the offline regression checks and reports from historical agent
evaluations. Those evaluations used a private harness, corpus, and raw runs, so
they cannot be reproduced from this repository.

## Development

See [development and release checks](https://github.com/cartertinney/mqtt-spec-mcp/blob/main/docs/DEVELOPMENT.md)
for running from a checkout, testing, rebuilding indexes, and releasing. Benchmark
checks are checkout-only and are not shipped with the server.

## License

The code is [MIT licensed](https://github.com/cartertinney/mqtt-spec-mcp/blob/main/LICENSE).
Specification text in the indexes and returned excerpts is copyright OASIS Open,
not MIT licensed. [NOTICE](https://github.com/cartertinney/mqtt-spec-mcp/blob/main/NOTICE)
contains the source attribution, transformation details, and OASIS notices.
