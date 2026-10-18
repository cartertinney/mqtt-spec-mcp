"""Porter (1980) suffix-stripping stemmer used to normalize search terms.

Implements the five steps of M. F. Porter, "An algorithm for suffix stripping",
Program 14(3), 1980. Stems are only ever compared with other stems, so the exact
output matters less than consistency: "closes", "closed" and "closing" all
become "close", and "connection", "connected" and "connecting" become "connect".
"""

from __future__ import annotations

from functools import lru_cache

_VOWELS = frozenset("aeiou")


def _is_consonant(word: str, i: int) -> bool:
    letter = word[i]
    if letter in _VOWELS:
        return False
    if letter == "y":
        return i == 0 or not _is_consonant(word, i - 1)
    return True


def _measure(stem: str) -> int:
    """m in [C](VC)^m[V]: the number of vowel-consonant sequences."""
    m = 0
    previous_vowel = False
    for i in range(len(stem)):
        consonant = _is_consonant(stem, i)
        if consonant and previous_vowel:
            m += 1
        previous_vowel = not consonant
    return m


def _has_vowel(stem: str) -> bool:
    return any(not _is_consonant(stem, i) for i in range(len(stem)))


def _ends_double_consonant(word: str) -> bool:
    return (
        len(word) >= 2 and word[-1] == word[-2] and _is_consonant(word, len(word) - 1)
    )


def _ends_cvc(word: str) -> bool:
    """*o: ends consonant-vowel-consonant, the last not w, x or y."""
    n = len(word)
    return (
        n >= 3
        and _is_consonant(word, n - 3)
        and not _is_consonant(word, n - 2)
        and _is_consonant(word, n - 1)
        and word[-1] not in "wxy"
    )


def _replace(word: str, rules: tuple[tuple[str, str], ...], min_m: int) -> str:
    """Apply the rule with the longest matching suffix, if its stem has m > min_m."""
    for suffix, replacement in rules:
        if word.endswith(suffix):
            stem = word[: -len(suffix)]
            return stem + replacement if _measure(stem) > min_m else word
    return word


_STEP2 = tuple(
    sorted(
        (
            ("ational", "ate"),
            ("tional", "tion"),
            ("enci", "ence"),
            ("anci", "ance"),
            ("izer", "ize"),
            ("abli", "able"),
            ("alli", "al"),
            ("entli", "ent"),
            ("eli", "e"),
            ("ousli", "ous"),
            ("ization", "ize"),
            ("ation", "ate"),
            ("ator", "ate"),
            ("alism", "al"),
            ("iveness", "ive"),
            ("fulness", "ful"),
            ("ousness", "ous"),
            ("aliti", "al"),
            ("iviti", "ive"),
            ("biliti", "ble"),
        ),
        key=lambda rule: -len(rule[0]),
    )
)
_STEP3 = tuple(
    sorted(
        (
            ("icate", "ic"),
            ("ative", ""),
            ("alize", "al"),
            ("iciti", "ic"),
            ("ical", "ic"),
            ("ful", ""),
            ("ness", ""),
        ),
        key=lambda rule: -len(rule[0]),
    )
)
_STEP4 = tuple(
    sorted(
        (
            "al",
            "ance",
            "ence",
            "er",
            "ic",
            "able",
            "ible",
            "ant",
            "ement",
            "ment",
            "ent",
            "ion",
            "ou",
            "ism",
            "ate",
            "iti",
            "ous",
            "ive",
            "ize",
        ),
        key=lambda suffix: -len(suffix),
    )
)


def _step1ab(word: str) -> str:
    if word.endswith(("sses", "ies")):
        word = word[:-2]
    elif word.endswith("ss"):
        pass
    elif word.endswith("s"):
        word = word[:-1]

    if word.endswith("eed"):
        if _measure(word[:-3]) > 0:
            word = word[:-1]
        return word
    for suffix in ("ed", "ing"):
        if word.endswith(suffix) and _has_vowel(word[: -len(suffix)]):
            word = word[: -len(suffix)]
            if word.endswith(("at", "bl", "iz")):
                return word + "e"
            if _ends_double_consonant(word) and word[-1] not in "lsz":
                return word[:-1]
            if _measure(word) == 1 and _ends_cvc(word):
                return word + "e"
            return word
    return word


def _step1c(word: str) -> str:
    if word.endswith("y") and _has_vowel(word[:-1]):
        return word[:-1] + "i"
    return word


def _step4(word: str) -> str:
    for suffix in _STEP4:
        if word.endswith(suffix):
            stem = word[: -len(suffix)]
            if _measure(stem) <= 1:
                return word
            if suffix == "ion" and not stem.endswith(("s", "t")):
                return word
            return stem
    return word


def _step5(word: str) -> str:
    if word.endswith("e"):
        stem = word[:-1]
        m = _measure(stem)
        if m > 1 or (m == 1 and not _ends_cvc(stem)):
            word = stem
    if word.endswith("ll") and _measure(word) > 1:
        word = word[:-1]
    return word


@lru_cache(maxsize=65536)
def porter_stem(word: str) -> str:
    """Stem one lowercase ASCII word; short words and words with digits are kept."""
    if len(word) <= 3 or not word.isalpha() or not word.isascii():
        return word
    word = _step1ab(word)
    word = _step1c(word)
    word = _replace(word, _STEP2, 0)
    word = _replace(word, _STEP3, 0)
    word = _step4(word)
    return _step5(word)
