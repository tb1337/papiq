"""Child process for one Docling conversion:
`python -m papiq.adapters.outbound.docling.convert SOURCE MARKDOWN STRUCTURE MODELS`.

Uses the text layer of the PDF (no OCR of its own), recognises layout and tables with the
models in MODELS and never downloads models. Prints `{"pages": n}` on success. Exit codes:
0 success, 3 the document cannot be processed, 4 models missing, 1 other errors; the last
line of standard error says why.
"""

import json
import os
import sys
from pathlib import Path

UNPROCESSABLE = 3
MODELS_MISSING = 4


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
    markdown.write_text(document.export_to_markdown(), encoding="utf-8")
    structure.write_text(json.dumps(document.export_to_dict()), encoding="utf-8")
    print(json.dumps({"pages": len(document.pages)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
