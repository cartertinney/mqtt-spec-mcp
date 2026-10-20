"""Parser and canonicalization unit tests on small synthetic HTML fixtures."""

from __future__ import annotations

from mqtt_spec_mcp.versions import PINS
from scripts.indexgen.build import Builder
from scripts.indexgen.canonical import canonicalize, decode_cfemail, sha256
from scripts.indexgen.parse import RawParagraph, RawTable, parse_document

BULLET = (
    "<span style='font-family:Symbol'>\xb7<span style='font:7.0pt \"Times New Roman\"'>"
    "&nbsp;&nbsp;&nbsp; </span></span>"
)


def page(body: str) -> str:
    return (
        f"<html><head><style>p {{color:red}}</style></head><body>{body}</body></html>"
    )


def encode_cfemail(email: str, key: int = 0x42) -> str:
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in email)


def test_merged_cells_expand_to_full_grid() -> None:
    html = page(
        "<h1>1 Intro</h1>"
        "<table>"
        "<tr><td colspan=2>Identifier</td><td rowspan=2>Name</td></tr>"
        "<tr><td>Dec</td><td>Hex</td></tr>"
        "<tr><td>1</td><td>0x01</td><td>One</td></tr>"
        "<tr><td>2</td><td colspan=2>Two wide</td></tr>"
        "</table>"
    )
    table = parse_document(html).sections[0].blocks[0]
    assert isinstance(table, RawTable)
    assert table.rows == [
        ["Identifier", "Identifier", "Name"],
        ["Dec", "Hex", "Name"],
        ["1", "0x01", "One"],
        ["2", "Two wide", "Two wide"],
    ]


def test_nested_table_content_is_not_a_separate_block() -> None:
    html = page(
        "<h1>1 Intro</h1><table><tr><td><p>outer</p>"
        "<table><tr><td>inner</td></tr></table></td></tr></table>"
    )
    blocks = parse_document(html).sections[0].blocks
    assert len(blocks) == 1
    assert isinstance(blocks[0], RawTable)
    assert blocks[0].rows == [["outer inner"]]


def test_change_marks_keep_inserted_and_drop_deleted_text() -> None:
    # The 3.1.1 Plus Errata 01 edition marks its correction with <ins> and <del>.
    html = page(
        "<h1>1 Intro</h1>"
        "<p><span class=msoIns><ins cite='x'>if (multiplier &gt; 128)</ins></span></p>"
        "<p><span class=msoIns><ins cite='x'>multiplier *= 128</ins></span></p>"
        "<p><span class=msoDel><del cite='x'>multiplier *= 128</del></span></p>"
        "<p><span class=msoDel><del cite='x'>if (multiplier &gt; 128)</del></span></p>"
        "<p>kept <del>removed </del>text</p>"
    )
    blocks = parse_document(html).sections[0].blocks
    texts = [b.text for b in blocks if isinstance(b, RawParagraph)]
    assert texts == ["if (multiplier > 128)", "multiplier *= 128", "kept text"]


def test_cloudflare_canonicalization_is_stable() -> None:
    email = "editor@example.org"
    clean = (
        b'<html><body><p>Contact <a href="mailto:editor@example.org">'
        b"editor@example.org</a></p>\r\n</body></html>"
    )
    obfuscated = (
        b'<html><body><p>Contact <a href="/cdn-cgi/l/email-protection#'
        + encode_cfemail(email, 0x17).encode()
        + b'"><span class="__cf_email__" data-cfemail="'
        + encode_cfemail(email, 0x5A).encode()
        + b'">[email&#160;protected]</span></a></p>'
        b'<script data-cfasync="false" '
        b'src="/cdn-cgi/scripts/5c5dd728/cloudflare-static/email-decode.min.js">'
        b"</script>"
        b'<script defer src="https://static.cloudflareinsights.com/beacon.min.js">'
        b"</script>\n\n  </body></html>"
    )
    assert decode_cfemail(encode_cfemail(email)) == email
    assert canonicalize(obfuscated) == canonicalize(clean)
    assert sha256(canonicalize(obfuscated)) == sha256(canonicalize(clean))
    assert canonicalize(canonicalize(clean)) == canonicalize(clean)


def test_non_cloudflare_scripts_are_kept() -> None:
    raw = b"<html><body><script>var x = 1;</script></body></html>"
    assert b"var x = 1;" in canonicalize(raw)


def test_311_pre_li_and_caption_headings() -> None:
    html = page(
        "<p class=MsoToc1>1 Intro ...... 5</p>"
        "<h1>1<span style='font:7.0pt \"Times New Roman\"'>&nbsp; </span>Intro</h1>"
        "<pre>[RFC793]   Postel, J.</pre>"
        "<ul><li>first&nbsp;item</li><li>second item</li></ul>"
        f"<p class=MsoListBullet>{BULLET}bulleted paragraph</p>"
        "<h5>Table 2.1 - Control packet types</h5>"
        "<table><tr><td>Name</td><td>Value</td></tr>"
        "<tr><td>CONNECT</td><td>1</td></tr></table>"
        "<h5>Figure 2.1 - Not a table</h5>"
        "<p style='display:none'>hidden</p>"
        "<p>soft\xadhyphen</p>"
        "<p class=AppendixHeading1>Appendix A. Acknowledgements</p>"
        "<p>appendix text</p>"
    )
    doc = parse_document(html)
    assert [s.id for s in doc.sections] == ["1"]
    section = doc.sections[0]
    assert section.title == "Intro"
    kinds = [
        (b.kind, b.text) if isinstance(b, RawParagraph) else ("table", b.caption)
        for b in section.blocks
    ]
    assert kinds == [
        ("paragraph", "[RFC793] Postel, J."),
        ("list_item", "first item"),
        ("list_item", "second item"),
        ("list_item", "bulleted paragraph"),
        ("table", "Table 2.1 - Control packet types"),
        ("paragraph", "Figure 2.1 - Not a table"),
        ("paragraph", "softhyphen"),
    ]
    assert doc.appendices[0].heading.startswith("Appendix A")
    assert all("appendix text" not in str(b) for b in section.blocks)


def test_parent_is_nearest_existing_ancestor() -> None:
    html = page("<h1>1 A</h1><h3>1.2.3 Deep</h3><h2>1.4 B</h2>")
    sections = parse_document(html).sections
    assert [(s.id, s.level, s.parent) for s in sections] == [
        ("1", 1, None),
        ("1.2.3", 3, "1"),
        ("1.4", 2, "1"),
    ]


def _glossary_doc(body: str) -> Builder:
    html = page(f"<h1>1 Introduction</h1><h2>1.2 Terminology</h2>{body}")
    return Builder(parse_document(html), PINS["5.0"])


def test_glossary_inline_and_multi_block_forms() -> None:
    builder = _glossary_doc(
        "<p>The key words MUST and MAY are described in RFC 2119.</p>"
        "<p>Client:</p><p>A program or device. A Client:</p>"
        f"<p class=MsoListParagraph>{BULLET}opens the connection</p>"
        "<p>Session: A stateful interaction.</p>"
        "<p>Disallowed Unicode code point:</p><p>Code points that must not appear.</p>"
    )
    terms = {t.name: t.definition for t in builder.terms()}
    assert terms == {
        "Client": "A program or device. A Client: opens the connection",
        "Session": "A stateful interaction.",
        "Disallowed Unicode code point": "Code points that must not appear.",
    }


def _codes_html() -> str:
    return page(
        "<h1>1 Intro</h1><h1>2 Format</h1><h2>2.4 Reason Code</h2>"
        "<table><tr><td>Reason Code</td><td>Hex</td><td>Name</td><td>Packets</td></tr>"
        "<tr><td>128</td><td>0x80</td><td>Unspecified error</td><td>SUBACK</td></tr>"
        "</table>"
        "<h1>3 Packets</h1><h2>3.2 CONNACK</h2><h3>3.2.2 Header</h3>"
        "<h4>3.2.2.3 Connect Return code</h4>"
        "<table><tr><td>Value</td><td>Return Code Response</td>"
        "<td>Description</td></tr>"
        "<tr><td>0</td><td>0x00 Connection Accepted</td><td>ok</td></tr>"
        "<tr><td>6-255</td><td></td><td>Reserved</td></tr></table>"
        "<h2>3.9 SUBACK</h2><h3>3.9.3 Payload</h3>"
        "<p>Allowed return codes:</p>"
        "<p>0x00 - Success - Maximum QoS 0<br>0x80 - Failure</p>"
    )


def test_return_codes_are_not_reason_codes() -> None:
    doc = parse_document(_codes_html())
    v311 = Builder(doc, PINS["3.1.1"])
    returns = [(c.packets, c.value, c.name) for c in v311.return_codes()]
    assert returns == [
        (("CONNACK",), 0, "Connection Accepted"),
        (("SUBACK",), 0, "Success - Maximum QoS 0"),
        (("SUBACK",), 128, "Failure"),
    ]
    assert v311.reason_codes() == []
    v50 = Builder(doc, PINS["5.0"])
    assert v50.return_codes() == []
    assert [(c.value, c.name) for c in v50.reason_codes()] == [
        (128, "Unspecified error")
    ]
