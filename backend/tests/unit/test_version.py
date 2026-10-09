"""Backend, migration client and web UI carry the same version."""

import json
import tomllib
from pathlib import Path

from papiq import __version__

ROOT = Path(__file__).parents[3]


def test_the_three_parts_share_one_version() -> None:
    backend = tomllib.loads((ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"))
    migration = tomllib.loads((ROOT / "migration/pyproject.toml").read_text(encoding="utf-8"))
    web = json.loads((ROOT / "web/package.json").read_text(encoding="utf-8"))
    assert backend["project"]["version"] == __version__
    assert migration["project"]["version"] == __version__
    assert web["version"] == __version__
