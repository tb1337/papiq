"""Parsing with Docling, run as a child process per document (see `convert`).

A process per document reloads the models each time (a few seconds), but can be stopped on
timeout and returns its memory. Outputs go to temporary files next to the targets and are
renamed when both are complete.
"""

import asyncio
import importlib.metadata
import json
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from papiq.adapters.outbound.docling import convert
from papiq.adapters.outbound.system import run_process
from papiq.core.domain.errors import UnprocessableDocumentError
from papiq.core.ports.parser import ParseResult


class DoclingParser:
    def __init__(self, *, models: Path, timeout: timedelta, threads: int | None = None) -> None:
        """`models`: directory with the Docling models (`docling-tools models download`).
        `threads`: CPU threads of one conversion; default: all cores."""
        self._models = models
        self._timeout = timeout
        self._env = None if threads is None else {"OMP_NUM_THREADS": str(threads)}

    async def parse(self, source: Path, *, markdown: Path, structure: Path) -> ParseResult:
        suffix = f".{uuid.uuid4().hex}.tmp"
        markdown_tmp = markdown.with_name(f".{markdown.name}{suffix}")
        structure_tmp = structure.with_name(f".{structure.name}{suffix}")
        try:
            completed = await run_process(
                [
                    sys.executable,
                    "-m",
                    convert.__name__,
                    str(source),
                    str(markdown_tmp),
                    str(structure_tmp),
                    str(self._models),
                ],
                timeout=self._timeout,
                env=self._env,
            )
            reason = _last_line(completed.stderr)
            if completed.returncode == convert.UNPROCESSABLE:
                raise UnprocessableDocumentError(reason)
            if completed.returncode != 0:
                raise RuntimeError(
                    f"docling failed with exit code {completed.returncode}: {reason}"
                )
            pages = int(json.loads(completed.stdout.strip().splitlines()[-1])["pages"])
            await asyncio.to_thread(markdown_tmp.replace, markdown)
            await asyncio.to_thread(structure_tmp.replace, structure)
        finally:
            for path in (markdown_tmp, structure_tmp):
                await asyncio.to_thread(path.unlink, missing_ok=True)
        return ParseResult(pages=pages, parser=f"docling {importlib.metadata.version('docling')}")


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no details"
