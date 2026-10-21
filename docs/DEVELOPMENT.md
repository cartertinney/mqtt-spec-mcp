# Development and release checks

## Local checks

Requires uv and Python 3.10+. A clean checkout needs no model credentials,
evaluation data, or source downloads to run these checks:

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run python -m benchmarks.cases
uv run python -m benchmarks.retrieval \
    --min-recall-at-1 0.70 --min-recall-at-5 0.60 \
    --min-hit-rate 0.90 --min-mrr 0.78 --min-lookup-success 1.0
uv build
```

The suite covers version isolation, real stdio communication, pagination,
parsing, completeness gates, rejection of invalid indexes, and the bundled-case
scorer and validator.

The server distribution contains only the runtime package, its two indexes, and
license metadata. The checkout-only [benchmarks](../benchmarks/README.md) run with
`python -m benchmarks.<module>`; the optional `benchmark` dependency group adds a
tokenizer for the microbenchmark. Agent evaluations use a separate private
harness and are not run from this repository.

## Installed-wheel smoke test

CI tests Python 3.10, 3.12, and 3.13. Each version also builds and installs the
wheel into a fresh environment outside the checkout, then exercises the console
entry point and MCP protocol with both bundled indexes.

To do the same locally, run from the repository root:

```bash
uv build
repo_dir="$PWD"
smoke_dir=$(mktemp -d)
uv venv "$smoke_dir/venv"
uv pip install --python "$smoke_dir/venv/bin/python" "$repo_dir"/dist/*.whl
(
    cd "$smoke_dir"
    "$smoke_dir/venv/bin/python" "$repo_dir/scripts/smoke_wheel.py"
)
```

The smoke script also checks distribution contents, notices, and entry points.
Do not run it with the editable checkout environment: that would not test the
installed artifact. Dependency installation needs registry access or a populated
cache; server queries do not need network access.

## Source provenance and index regeneration

The two generated indexes are in [the runtime data directory](../src/mqtt_spec_mcp/data/).
Their source URLs, publication dates, and canonical SHA-256 hashes are pinned in
[versions.py](../src/mqtt_spec_mcp/versions.py).

```bash
uv run python -m scripts.generate_index --version 5.0
uv run python -m scripts.generate_index --version 3.1.1 \
    --source ./mqtt-v3.1.1-errata01-os-complete.html
```

Without `--source`, the generator downloads the selected publication. It
canonicalizes Cloudflare rewrites, verifies the version's hash, parses
Windows-1252 HTML, and writes atomically only after passing completeness checks:

| Count | MQTT 3.1.1 | MQTT 5.0 |
| --- | ---: | ---: |
| Numbered sections | 162 | 291 |
| Control packets | 14 | 15 |
| Properties | 0 | 27 |
| Reason-code entries | 0 | 45 |
| Return-code entries | 10 | 0 |
| Requirement labels | 141 | 252 |
| Glossary terms | 9 | 15 |

MQTT 3.1.1 uses the December 2015 edition incorporating Errata 01. Its corrected
Remaining Length pseudocode replaces the deleted text rather than indexing both.

`--allow-source-mismatch` is only for reviewing a changed source. The loader
rejects mismatched metadata until the pin is deliberately updated. Review changes
to both the index and attribution; do not update a hash merely to make a check pass.

[DECISIONS.md](DECISIONS.md) records canonicalization, parsing, label reconciliation,
and the historical evidence behind search and agent-guidance decisions.

## Releasing

[release.yml](../.github/workflows/release.yml) publishes to PyPI with
[trusted publishing](https://docs.pypi.org/trusted-publishers/): GitHub Actions
exchanges a short-lived OIDC token for an upload token, so the repository stores
no PyPI credentials, and each upload carries signed build attestations.

Its build job checks that the release tag matches the package version, runs the
tests, builds the sdist and wheel, and checks them with `twine check` and the
installed-wheel smoke test before saving them for upload. Only the publish jobs
can request a token, and they upload only those saved files.

### One-time setup

1. On PyPI, open Account settings → Publishing and add a pending GitHub
   publisher with:
   - PyPI project name: `mqtt-spec-mcp`
   - Owner: `cartertinney`
   - Repository name: `mqtt-spec-mcp`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

   A pending publisher does not reserve the name until its first upload.
2. On [TestPyPI](https://test.pypi.org/), add the same publisher with
   environment name `testpypi`.
3. In this repository's Settings → Environments, create `pypi` with yourself as a
   required reviewer, and create `testpypi`.

### Each release

1. Bump the version with `uv version --bump patch` (or `minor`), which updates
   `pyproject.toml` and `uv.lock`. Commit the change to `main` and wait for CI.
2. Optionally, run the Release workflow from the Actions tab. It uploads the
   current version to TestPyPI; each version can be uploaded only once there.
3. Publish a GitHub release whose tag is `v` plus the version (for example
   `v0.2.0`), using generated release notes:
   `gh release create v0.2.0 --generate-notes`.
4. Approve the `pypi` deployment when the workflow asks, then confirm the release
   at <https://pypi.org/p/mqtt-spec-mcp>.

## Before publishing

- Run the checks above from a clean checkout and test the installed wheel.
- Verify [NOTICE](../NOTICE) against the actual indexed publications; code is MIT
  licensed, OASIS text is not.
- Review tracked files and the history reachable from branches/tags that will be
  published for credentials, private logs, and machine-specific paths.
  Gitignore rules do not remove previously committed data.
- Keep evaluation corpora, raw runs, judge verdicts, the evaluation harness, and
  local authentication out of this repository. Back up private research
  artifacts separately; this repository is not their backup.
- Preserve historical plans and reports. New evaluations need new plans, and a
  new independent claim needs fresh held-out questions.
- Describe historical evaluations as private-data evaluations, not a reproducible
  public benchmark or a guarantee of agent accuracy.

Do not rewrite published history or upload private evaluation artifacts as part
of routine cleanup.
