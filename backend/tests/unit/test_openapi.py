"""The OpenAPI document the web UI's client is generated from (`web/openapi.json`)."""

from pathlib import Path

from papiq.composition.openapi import main, openapi_document, render

CHECKED_IN = Path(__file__).resolve().parents[3] / "web" / "openapi.json"


def test_the_web_ui_has_the_current_openapi_document() -> None:
    assert CHECKED_IN.read_text(encoding="utf-8") == render(openapi_document()), (
        "web/openapi.json is out of date: run `uv run python -m papiq.composition.openapi "
        "../web/openapi.json` in backend/, then `pnpm gen:api` in web/"
    )


def test_the_command_writes_the_document(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    assert main([str(target)]) == 0
    assert target.read_text(encoding="utf-8") == render(openapi_document())


def test_the_command_refuses_extra_arguments() -> None:
    assert main(["a", "b"]) == 2
