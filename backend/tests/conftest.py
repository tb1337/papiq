from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by directory: `tests/unit` is `unit`, `tests/integration` is `integration`."""
    for item in items:
        relative = Path(item.path).relative_to(_TESTS_DIR)
        if relative.parts[0] in {"unit", "integration"}:
            item.add_marker(getattr(pytest.mark, relative.parts[0]))
