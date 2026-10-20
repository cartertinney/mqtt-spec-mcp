"""Pinned identity of each supported MQTT publication.

These values are the single source of truth for what each bundled index must
claim to be. The loader rejects any index whose metadata differs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from .schema import INDEX_FORMAT_VERSION, Metadata

VersionId = Literal["3.1.1", "5.0"]
SUPPORTED_VERSIONS: tuple[VersionId, ...] = get_args(VersionId)


@dataclass(frozen=True)
class Pin:
    version: VersionId
    title: str
    published: str
    editors: tuple[str, ...]
    source_url: str
    source_sha256: str
    copyright: str
    data_file: str

    def metadata(self) -> Metadata:
        return Metadata(
            format_version=INDEX_FORMAT_VERSION,
            title=self.title,
            version=self.version,
            published=self.published,
            source_url=self.source_url,
            source_sha256=self.source_sha256,
            copyright=self.copyright,
        )


PINS: dict[VersionId, Pin] = {
    "3.1.1": Pin(
        version="3.1.1",
        title="MQTT Version 3.1.1",
        # MQTT Version 3.1.1 Plus Errata 01 (OASIS Standard Incorporating Approved
        # Errata 01): the 2014 standard with its one approved erratum applied.
        published="2015-12-10",
        editors=("Andrew Banks", "Rahul Gupta"),
        source_url=(
            "https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/errata01/os/"
            "mqtt-v3.1.1-errata01-os-complete.html"
        ),
        source_sha256="82bbab8153f69854f240dfccb0bcd9457eb058feb268a9b51ab3db43a5ee8dc4",
        copyright="Copyright © OASIS Open 2015. All Rights Reserved.",
        data_file="mqtt-v3.1.1.json",
    ),
    "5.0": Pin(
        version="5.0",
        title="MQTT Version 5.0",
        published="2019-03-07",
        editors=("Andrew Banks", "Ed Briggs", "Ken Borgendale", "Rahul Gupta"),
        source_url="https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html",
        source_sha256="623cd5b41e73c7518cb9ccce235e45b897af9e5a891d3534309f85bd118a680c",
        copyright="Copyright © OASIS Open 2019. All Rights Reserved.",
        data_file="mqtt-v5.0.json",
    ),
}


def as_version(value: str) -> VersionId:
    if value not in SUPPORTED_VERSIONS:
        raise ValueError(f"unsupported MQTT version {value!r}")
    return value
