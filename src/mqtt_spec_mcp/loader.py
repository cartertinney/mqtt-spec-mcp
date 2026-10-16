"""Load and verify the bundled per-version indexes."""

from __future__ import annotations

from importlib import resources

from pydantic import ValidationError

from .schema import SpecIndex
from .versions import PINS, SUPPORTED_VERSIONS, as_version


class IndexLoadError(RuntimeError):
    pass


def parse_index(raw: str | bytes, version: str) -> SpecIndex:
    """Strictly validate an index and require it to match the version's pin."""
    if version not in SUPPORTED_VERSIONS:
        raise IndexLoadError(f"unsupported MQTT version {version!r}")
    try:
        index = SpecIndex.model_validate_json(raw, strict=True)
    except ValidationError as exc:
        raise IndexLoadError(f"MQTT {version} index failed validation: {exc}") from exc
    expected = PINS[as_version(version)].metadata()
    if index.metadata != expected:
        diffs = [
            name
            for name in type(expected).model_fields
            if getattr(index.metadata, name) != getattr(expected, name)
        ]
        raise IndexLoadError(
            f"MQTT {version} index metadata does not match the pinned publication "
            f"(fields: {', '.join(diffs)})"
        )
    return index


def load_index(version: str) -> SpecIndex:
    if version not in SUPPORTED_VERSIONS:
        raise IndexLoadError(f"unsupported MQTT version {version!r}")
    pin = PINS[as_version(version)]
    data_dir = resources.files("mqtt_spec_mcp").joinpath("data")
    data = data_dir.joinpath(pin.data_file).read_bytes()
    return parse_index(data, version)
