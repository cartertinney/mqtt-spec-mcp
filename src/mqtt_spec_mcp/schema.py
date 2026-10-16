"""Strict Pydantic schema for the bundled per-version index files."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

INDEX_FORMAT_VERSION = 1


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Metadata(_Strict):
    format_version: int
    title: str
    version: str
    published: str
    source_url: str
    source_sha256: str
    copyright: str


class Paragraph(_Strict):
    type: Literal["paragraph"]
    text: str


class ListItem(_Strict):
    type: Literal["list_item"]
    text: str


class Table(_Strict):
    type: Literal["table"]
    caption: str | None
    rows: tuple[tuple[str, ...], ...]


Block = Annotated[Paragraph | ListItem | Table, Field(discriminator="type")]


class Section(_Strict):
    id: str
    title: str
    level: int
    parent: str | None
    blocks: tuple[Block, ...]
    requirements: tuple[str, ...]


class Packet(_Strict):
    name: str
    value: int
    direction: str
    description: str
    fixed_header_flags: str
    section: str


class Property(_Strict):
    id: int
    hex: str
    name: str
    data_type: str
    packets: tuple[str, ...]
    sections: tuple[str, ...]


class Code(_Strict):
    value: int
    hex: str
    name: str
    packets: tuple[str, ...]
    section: str


class Requirement(_Strict):
    label: str
    section: str
    excerpt: str


class Term(_Strict):
    name: str
    definition: str
    section: str


class SpecIndex(_Strict):
    metadata: Metadata
    sections: tuple[Section, ...]
    packets: tuple[Packet, ...]
    properties: tuple[Property, ...]
    reason_codes: tuple[Code, ...]
    return_codes: tuple[Code, ...]
    requirements: tuple[Requirement, ...]
    terms: tuple[Term, ...]
