"""Generates the sample files in this directory: `uv run python tests/samples/make_samples.py`.

- scan.pdf        a scanned page: one image, no text layer
- text.pdf        a page with a text layer (born-digital)
- photo.jpg       a photo of a page, without DPI information
- blank.pdf       a scanned empty page: OCR finds no text
- damaged.pdf     a PDF header followed by garbage
- unsupported.docx  an Office file (a ZIP archive), not supported
"""

import io
import random
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent

LINES = ["Rechnung Nummer 4711", "Papiq Testdokument", "Betrag 123,45 EUR"]


def page(lines: list[str], size: tuple[int, int] = (1240, 1754)) -> Image.Image:
    """An A4 page at 150 dpi with large black text."""
    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=56)
    for number, line in enumerate(lines):
        draw.text((120, 200 + number * 110), line, fill=0, font=font)
    return image


def text_pdf(lines: list[str]) -> bytes:
    """A minimal PDF with Helvetica text, written by hand."""
    content = (
        "BT /F1 24 Tf 72 760 Td " + " ".join(f"({line}) Tj 0 -36 Td" for line in lines) + " ET"
    )
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def main() -> None:
    page(LINES).save(HERE / "scan.pdf", resolution=150)
    (HERE / "text.pdf").write_bytes(text_pdf(LINES))
    page(LINES, (1000, 1300)).convert("RGB").save(HERE / "photo.jpg", quality=80)
    page([]).save(HERE / "blank.pdf", resolution=150)
    (HERE / "damaged.pdf").write_bytes(b"%PDF-1.7\n" + random.Random(4711).randbytes(2000))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as docx:
        docx.writestr("[Content_Types].xml", "<Types/>")
        docx.writestr("word/document.xml", "<document/>")
    (HERE / "unsupported.docx").write_bytes(archive.getvalue())


if __name__ == "__main__":
    main()
