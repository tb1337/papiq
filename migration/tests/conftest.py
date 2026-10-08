import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from papiq_migration import fake_paperless
from papiq_migration.cli import execute
from papiq_migration.config import Config
from papiq_migration.fake_paperless import Archive, FakePaperless, sample_archive
from tests import fake_papiq
from tests.fake_papiq import FakePapiq

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@dataclass
class Setup:
    directory: Path
    archive: Archive
    paperless: FakePaperless
    papiq: FakePapiq
    messages: list[str] = field(default_factory=list)

    def config(self, **changes: object) -> Config:
        values: dict[str, object] = {
            "paperless_url": "http://paperless",
            "papiq_url": "http://papiq",
            "state": self.directory / "state.sqlite",
            "report_dir": self.directory / "reports",
            "paperless_token": fake_paperless.TOKEN,
            "papiq_token": fake_papiq.TOKEN,
            "concurrency": 3,
        }
        return Config(**{**values, **changes})  # type: ignore[arg-type]

    async def run(
        self, command: str, *, stop: asyncio.Event | None = None, **changes: object
    ) -> int:
        return await execute(
            self.config(**changes),
            command,
            stop=stop,
            paperless_transport=self.paperless.transport(),
            papiq_transport=self.papiq.transport(),
            progress=self.messages.append,
            poll=(0.0, 0.0),
            delays=(0.0, 0.0, 0.0),
        )

    def report(self, command: str) -> dict[str, Any]:
        import json

        return cast(
            "dict[str, Any]",
            json.loads((self.directory / "reports" / f"{command}.json").read_text()),
        )


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    archive = sample_archive(PDF)
    return Setup(tmp_path, archive, FakePaperless(archive), FakePapiq())
