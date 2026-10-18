"""Text normalization helpers shared by the generator and the runtime."""

from __future__ import annotations

import re
import unicodedata

from .stemmer import porter_stem

_DASHES = dict.fromkeys(
    map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"), "-"
)
_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[0-9a-z]+")
_NATURAL = re.compile(r"(\d+)")

# Reference words agents put in front of a section number: "Section 3.1", "§3.1".
SECTION_PREFIX = re.compile(
    r"^(?:§+\s*|(?:sections?|sect\.|sec\.|chapter|ch\.)\s*(?=\d))", re.IGNORECASE
)
# A requirement label written without its "MQTT-" prefix, e.g. "3.1.2-4".
_BARE_LABEL = re.compile(r"\d+(?:\.\d+)+\.?-\d+")

LABEL_PATTERN = re.compile(r"\[\s*(MQTT\s*-[0-9.\-\s]*[0-9])\s*\]", re.IGNORECASE)

# Printed 3.1.1 labels that are indexed under a different canonical spelling.
LABEL_ALIASES: dict[str, dict[str, str]] = {
    "3.1.1": {"MQTT-3.3.1.-1": "MQTT-3.3.1-1"},
    "5.0": {},
}


def collapse_ws(text: str) -> str:
    return _WS.sub(" ", text.replace("\xa0", " ").replace("\xad", "")).strip()


def normalize(text: str) -> str:
    """Matching form: NFKC, case-folded, unified dashes, collapsed whitespace."""
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).casefold()
    return _WS.sub(" ", text).strip()


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(normalize(text))


# Words whose Porter stem would merge distinct MQTT concepts: "connection" would
# become "connect" and match every CONNECT packet section title.
STEM_EXCEPTIONS = {"connection": "connection", "connections": "connection"}


def stem(token: str) -> str:
    """Search stem: "closes", "closed" and "closing" all become "close"."""
    return STEM_EXCEPTIONS.get(token) or porter_stem(token)


def search_tokens(text: str) -> list[str]:
    return [stem(t) for t in tokenize(text)]


def clean_label(raw: str) -> str:
    """Strip brackets, whitespace and trailing punctuation; uppercase; unify dashes."""
    text = unicodedata.normalize("NFKC", raw).translate(_DASHES)
    text = re.sub(r"[\s\[\]]+", "", text).upper().rstrip(".,;:")
    return f"MQTT-{text}" if _BARE_LABEL.fullmatch(text) else text


def canonical_label(raw: str, version: str) -> str:
    label = clean_label(raw)
    return LABEL_ALIASES.get(version, {}).get(label, label)


def natural_key(text: str) -> tuple[tuple[int, int | str], ...]:
    parts = _NATURAL.split(text)
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts if p)


def looks_like_label(text: str) -> bool:
    return re.fullmatch(r"MQTT-[0-9][0-9.\-]*[0-9]", clean_label(text)) is not None
