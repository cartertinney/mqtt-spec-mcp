"""Text normalization helpers shared by the generator and the runtime."""

from __future__ import annotations

import re
import unicodedata

_DASHES = dict.fromkeys(
    map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"), "-"
)
_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[0-9a-z]+")
_NATURAL = re.compile(r"(\d+)")

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


def stem(token: str) -> str:
    """Very light plural folding so 'Subscriptions' matches 'Subscription'."""
    if (
        len(token) > 3
        and token.endswith("s")
        and not token.endswith(("ss", "us", "is"))
    ):
        return token[:-1]
    return token


def search_tokens(text: str) -> list[str]:
    return [stem(t) for t in tokenize(text)]


def clean_label(raw: str) -> str:
    """Strip brackets and whitespace, uppercase, unify dashes."""
    text = unicodedata.normalize("NFKC", raw).translate(_DASHES)
    return re.sub(r"[\s\[\]]+", "", text).upper()


def canonical_label(raw: str, version: str) -> str:
    label = clean_label(raw)
    return LABEL_ALIASES.get(version, {}).get(label, label)


def natural_key(text: str) -> tuple[tuple[int, int | str], ...]:
    parts = _NATURAL.split(text)
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts if p)


def looks_like_label(text: str) -> bool:
    return re.fullmatch(r"MQTT-[0-9][0-9.\-]*[0-9]", clean_label(text)) is not None
