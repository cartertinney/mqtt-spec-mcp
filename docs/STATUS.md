# Limitations and roadmap

## Supported scope

This is an offline, read-only reference server for MQTT 3.1.1 Plus Errata 01 and
MQTT 5.0. It is not a broker, a conformance test suite, or a source of implementation
advice beyond the indexed specifications. OASIS publications remain authoritative.

Each call is version-specific. The server cannot prevent an agent from mixing
versions or making an unsupported inference after receiving correct evidence.

## Known limitations

- Search returns excerpts, not an exhaustive proof that all applicable rules have
  been found. Deciding rules and exceptions can be cut or ranked outside a pack.
- Agents can omit or misread rules already present in the evidence.
- Excerpt markers identify sections shown below a 95% coverage threshold; an
  unmarked section is not a formal completeness guarantee.
- Token reductions are workload- and model-dependent. Fewer tokens do not
  necessarily mean lower cost, especially with prompt-cache pricing.
- Historical model evaluations used a private harness, an external corpus, and
  private raw runs. Public reports cannot be regenerated from this repository.
- The historical test split was used under two release plans. It should not be
  treated as fresh held-out evidence for future changes.
- Tight benchmark tool budgets do not represent every real-world agent client,
  and uncontrolled model-service timing is not a server latency measurement.

See [evaluation methods and availability](../benchmarks/README.md) for the
historical results and their qualifications.

## Roadmap

1. Build an offline evidence-coverage benchmark on the evaluation corpus's dev
   split before tuning more model prompts. It needs private data, so it belongs
   with the private evaluation harness.
2. Improve context around decisive rules: whole paragraphs, list lead-ins, and
   links to exceptions, while keeping response sizes bounded.
3. Reduce unnecessary context on simple lookups and single-section questions.
4. Validate future changes on fresh independently authored held-out questions.
5. Test representative real client workflows as well as the controlled harness.

These are directions, not release commitments. The detailed
[2026-10-21 research snapshot](archive/STATUS-2026-10-21.md) is historical context,
not the contributor setup guide. Current commands are in
[DEVELOPMENT.md](DEVELOPMENT.md) and the [benchmarks guide](../benchmarks/README.md).
