"""Generate a bundled MQTT specification index from the pinned OASIS HTML.

Usage::

    uv run python -m scripts.generate_index --version 5.0
    uv run python -m scripts.generate_index --version 3.1.1 \
        --source ./mqtt-v3.1.1-os.html

Only the selected version's pin is checked. Nothing is written unless the
source checksum matches the pin and the completeness gate passes.
``--allow-source-mismatch`` exists for deliberate review of a changed source:
the resulting index records the actual checksum, so the server will refuse to
load it until the pin in ``mqtt_spec_mcp.versions`` is updated on purpose.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

from mqtt_spec_mcp.versions import PINS, SUPPORTED_VERSIONS, Pin

from .indexgen.build import build_index
from .indexgen.canonical import canonicalize, sha256
from .indexgen.completeness import check_completeness
from .indexgen.parse import parse_document

DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "mqtt_spec_mcp" / "data"
USER_AGENT = "mqtt-spec-mcp-index-generator/1 (+https://www.oasis-open.org/)"


class GenerationError(RuntimeError):
    pass


def read_source(source: str) -> bytes:
    if source.startswith(("http://", "https://")):
        headers = {"User-Agent": USER_AGENT}
        request = urllib.request.Request(source, headers=headers)  # noqa: S310
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            data: bytes = response.read()
            return data
    return Path(source).read_bytes()


def generate(
    pin: Pin, raw: bytes, output: Path, *, allow_source_mismatch: bool = False
) -> dict[str, int]:
    canonical = canonicalize(raw)
    digest = sha256(canonical)
    if digest != pin.source_sha256:
        if not allow_source_mismatch:
            raise GenerationError(
                f"MQTT {pin.version} source checksum mismatch: expected "
                f"{pin.source_sha256}, got {digest}. Nothing was written. Use "
                "--allow-source-mismatch only to deliberately review a changed source."
            )
        print(
            f"warning: MQTT {pin.version} source checksum {digest} does not match the "
            "pin; the output will not load until the pin is updated.",
            file=sys.stderr,
        )
    document = parse_document(canonical.decode("cp1252"))
    index = build_index(document, pin)
    if digest != pin.source_sha256:
        metadata = index.metadata.model_copy(update={"source_sha256": digest})
        index = index.model_copy(update={"metadata": metadata})
    check_completeness(index, pin.version)

    payload = index.model_dump_json(indent=None) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        Path(tmp).chmod(0o644)
        Path(tmp).replace(output)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return {
        "sections": len(index.sections),
        "packets": len(index.packets),
        "properties": len(index.properties),
        "reason_codes": len(index.reason_codes),
        "return_codes": len(index.return_codes),
        "requirements": len(index.requirements),
        "terms": len(index.terms),
    }


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", choices=SUPPORTED_VERSIONS, default="5.0")
    parser.add_argument("--source", help="URL or local file (default: pinned URL)")
    parser.add_argument("--output", type=Path, help="output JSON path")
    parser.add_argument("--allow-source-mismatch", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pin = PINS[args.version]
    output: Path = args.output or DATA_DIR / pin.data_file
    try:
        raw = read_source(args.source or pin.source_url)
        counts = generate(
            pin, raw, output, allow_source_mismatch=args.allow_source_mismatch
        )
    except Exception as exc:  # report any failure without writing
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {output} (MQTT {pin.version})")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
