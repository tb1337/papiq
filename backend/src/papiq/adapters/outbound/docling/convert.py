"""Child process for one Docling conversion:
`python -m papiq.adapters.outbound.docling.convert SOURCE MARKDOWN STRUCTURE MODELS`.

Uses the text layer of the PDF (no OCR of its own), recognises layout and tables with the
models in MODELS and never downloads models. Prints `{"pages": n, "note": ...}` on success
(`note` is null unless the fallback below was used). Exit codes: 0 success, 3 the document
cannot be processed, 4 models missing, 1 other errors; the last line of standard error says why.

The layout model sometimes takes a whole scanned page for a picture (a payslip, a form) and the
Markdown has no text, although OCR did recognise some. Then the text layer of the archive is
written as plain Markdown instead, one block per page, and the note says so; the structure
stays Docling's.
"""

import json
import os
import re
import sys
from pathlib import Path

from papiq.adapters.outbound.pdfium.library import text_layer

UNPROCESSABLE = 3
MODELS_MISSING = 4
TEXT_LAYER_NOTE = (
    "the layout analysis found no text; the plain text layer of the archive was used instead"
)

# Markdown without any letter or digit has no text; comments are placeholders (`<!-- image -->`).
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_WORD = re.compile(r"\w")


def markdown_or_text_layer(markdown: str, source: Path) -> tuple[str, str | None]:
    """Docling's Markdown, or the text layer of `source` with the note if the Markdown has no
    text (and the text layer has)."""
    if _WORD.search(_COMMENT.sub("", markdown)) is not None:
        return markdown, None
    pages = [" ".join(page.split()) for page in text_layer(source)]
    if not any(pages):
        return markdown, None
    return "\n\n".join(page for page in pages if page) + "\n", TEXT_LAYER_NOTE


def main(argv: list[str]) -> int:
    source, markdown, structure, models = (Path(arg) for arg in argv)
    if not models.is_dir():
        print(f"Docling models not found in {models}", file=sys.stderr)
        return MODELS_MISSING
    os.environ["HF_HUB_OFFLINE"] = "1"  # never download at run time

    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.exceptions import ConversionError

    options = PdfPipelineOptions(artifacts_path=models, do_ocr=False, do_table_structure=True)
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    try:
        result = converter.convert(source, raises_on_error=False)
    except ConversionError as error:
        print(f"the PDF cannot be parsed: {error}", file=sys.stderr)
        return UNPROCESSABLE
    if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
        errors = "; ".join(item.error_message for item in result.errors) or result.status.value
        print(f"the PDF cannot be parsed: {errors}", file=sys.stderr)
        return UNPROCESSABLE
    document = result.document
    text, note = markdown_or_text_layer(document.export_to_markdown(), source)
    markdown.write_text(text, encoding="utf-8")
    structure.write_text(json.dumps(document.export_to_dict()), encoding="utf-8")
    print(json.dumps({"pages": len(document.pages), "note": note}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
