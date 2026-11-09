"""A minimal, deterministic text PDF writer, so CV generation needs no extra dependency."""

import textwrap

_LINES_PER_PAGE = 52
_LINE_WIDTH = 92


def _escape(text: str) -> bytes:
    cleaned = text.encode("latin-1", "replace")
    return cleaned.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _page_stream(lines: list[str]) -> bytes:
    parts = [b"BT /F1 10 Tf 13 TL 50 790 Td"]
    parts += [b"(" + _escape(line) + b") Tj T*" for line in lines]
    parts.append(b"ET")
    return b"\n".join(parts)


def text_to_pdf(text: str) -> bytes:
    wrapped: list[str] = []
    for line in text.splitlines():
        wrapped += textwrap.wrap(line, _LINE_WIDTH) or [""]
    pages = [
        wrapped[start : start + _LINES_PER_PAGE]
        for start in range(0, len(wrapped), _LINES_PER_PAGE)
    ] or [[""]]

    # Objects: 1 catalog, 2 page tree, 3 font, then a page and a content stream per page.
    page_ids = [4 + index * 2 for index in range(len(pages))]
    kids = b" ".join(b"%d 0 R" % page_id for page_id in page_ids)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(pages),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, lines in zip(page_ids, pages, strict=True):
        stream = _page_stream(lines)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (page_id + 1)
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    output += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(output)
