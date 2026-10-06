from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by directory: `tests/unit` is `unit`, `tests/integration` is `integration`."""
    for item in items:
        relative = Path(item.path).relative_to(_TESTS_DIR)
        kind = relative.parts[0]
        if kind not in {"unit", "integration"}:
            raise pytest.UsageError(f"{relative}: tests belong in tests/unit or tests/integration")
        item.add_marker(getattr(pytest.mark, kind))
