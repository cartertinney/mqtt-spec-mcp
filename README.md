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

## Setup

Your MCP client starts the server on your machine and talks to it over stdio.
There is no separate install step: configure the client to run
`uvx mqtt-spec-mcp`, and [uv](https://docs.astral.sh/uv/) downloads the package
from PyPI the first time, caches it, and starts it. uv also fetches a compatible
Python (3.10 or newer) if you don't have one. Each time the client starts the
server, uv checks PyPI for a newer release, so updates arrive automatically.

1. [Install uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Add the server to your client, as shown below. The server needs no
   environment variables, configuration files, or flags.

The server is also listed in the [MCP Registry](https://registry.modelcontextprotocol.io)
as `io.github.cartertinney/mqtt-spec-mcp`, for clients that install from it.

### Claude Code, Codex, GitHub Copilot CLI, and Gemini CLI

Each of these adds the server for all your projects with one command:

```bash
claude mcp add --scope user mqtt-spec -- uvx mqtt-spec-mcp   # Claude Code
codex mcp add mqtt-spec -- uvx mqtt-spec-mcp                 # OpenAI Codex CLI and IDE extension
copilot mcp add mqtt-spec -- uvx mqtt-spec-mcp               # GitHub Copilot CLI
gemini mcp add --scope user mqtt-spec uvx mqtt-spec-mcp      # Gemini CLI
```

### VS Code (GitHub Copilot)

Add the server to your user profile from a terminal:

```bash
code --add-mcp '{"name":"mqtt-spec","type":"stdio","command":"uvx","args":["mqtt-spec-mcp"]}'
```

Or run **MCP: Add Server** from the Command Palette, or add it to one project's
`.vscode/mcp.json`:

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

### Claude Desktop, Cursor, and other clients

Most other clients read the same `mcpServers` entry:

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

- **Claude Desktop:** Settings → Developer → Edit Config opens
  `claude_desktop_config.json`. Quit and reopen Claude after saving.
- **Cursor:** use `~/.cursor/mcp.json` for all projects, or `.cursor/mcp.json` in
  one project.
- **Zed:** put the same entry under `context_servers` in its settings file.

### ChatGPT and the OpenAI Agents SDK

ChatGPT connects only to remote MCP servers over HTTPS, so it can't start this
local server; use the Codex CLI or IDE extension instead (above). In an agent
built with the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/mcp/),
connect with `MCPServerStdio(params={"command": "uvx", "args": ["mqtt-spec-mcp"]})`.

### Without uv

Install the package with `pipx install mqtt-spec-mcp`, or with
`pip install mqtt-spec-mcp` in a Python 3.10+ environment. Then configure your
client to run the command `mqtt-spec-mcp`, with no arguments, instead of
`uvx mqtt-spec-mcp`.

### Troubleshooting and upgrades

- **The client can't find `uvx`.** Desktop apps often don't see your shell's
  `PATH`. Use the absolute path printed by `which uvx` (macOS and Linux) or
  `where uvx` (Windows).
- **The first start times out.** The first launch downloads the package, which
  can take a few seconds. Download it ahead of time with
  `uvx --from mqtt-spec-mcp python -c pass`.
- **Updates.** A new release is used from the next time your client starts the
  server. uv reuses PyPI's answer for up to 10 minutes, so a release can take
  that long to appear. After an update, start a new conversation so the client
  drops cached tool schemas. To stay on one version, use `mqtt-spec-mcp@0.1.0`
  in place of `mqtt-spec-mcp` in the client's arguments.
- **No network access.** The server works offline, but starting it with `uvx`
  needs to reach PyPI unless uv checked within the last 10 minutes. To run
  without network access, install it once with `uv tool install mqtt-spec-mcp`
  and configure the client to run `mqtt-spec-mcp`. `uvx mqtt-spec-mcp` then runs
  the installed copy as well, and updates come only from
  `uv tool upgrade mqtt-spec-mcp`.

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
