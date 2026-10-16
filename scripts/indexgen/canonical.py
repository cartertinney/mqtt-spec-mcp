"""Canonicalize downloaded OASIS HTML so that the pinned checksum is stable.

The OASIS site is served through Cloudflare, which may rewrite e-mail
addresses into obfuscated links and inject scripts. Those rewrites vary per
response, so they are undone before hashing:

1. ``data-cfemail`` spans/links are decoded back to plain text or ``mailto:``.
2. Cloudflare-injected ``<script>`` elements are removed.
3. Whitespace before ``</body>`` is normalized to a single CRLF.
"""

from __future__ import annotations

import hashlib
import re

_CF_LINK = re.compile(
    rb"<a\b([^>]*?)href=\"?/cdn-cgi/l/email-protection#([0-9a-fA-F]+)\"?([^>]*)>",
    re.IGNORECASE,
)
_CF_SPAN = re.compile(
    rb"<(span|a)\b[^>]*?class=\"?__cf_email__\"?[^>]*?data-cfemail=\"?([0-9a-fA-F]+)\"?"
    rb"[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT = re.compile(rb"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_CF_MARKERS = (b"cdn-cgi", b"__CF$cv$params", b"cloudflare", b"cf-beacon")
_BODY_END = re.compile(rb"\s*</body>", re.IGNORECASE)


def decode_cfemail(encoded: str | bytes) -> str:
    hex_text = encoded.decode("ascii") if isinstance(encoded, bytes) else encoded
    data = bytes.fromhex(hex_text)
    if not data:
        return ""
    key = data[0]
    return bytes(b ^ key for b in data[1:]).decode("latin-1")


def _replace_span(match: re.Match[bytes]) -> bytes:
    return decode_cfemail(match.group(2)).encode("cp1252", errors="replace")


def _replace_link(match: re.Match[bytes]) -> bytes:
    email = decode_cfemail(match.group(2)).encode("cp1252", errors="replace")
    attrs = (match.group(1) + match.group(3)).strip()
    sep = b" " if attrs else b""
    return b"<a " + attrs + sep + b'href="mailto:' + email + b'">'


def _replace_script(match: re.Match[bytes]) -> bytes:
    script = match.group(0)
    return b"" if any(m in script for m in _CF_MARKERS) else script


def canonicalize(raw: bytes) -> bytes:
    text = _CF_SPAN.sub(_replace_span, raw)
    text = _CF_LINK.sub(_replace_link, text)
    text = _SCRIPT.sub(_replace_script, text)
    return _BODY_END.sub(b"\r\n</body>", text, count=1)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
