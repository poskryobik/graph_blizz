import pytest

from backend.parsers import (
    MarkdownParser,
    PlainTextParser,
    UnsupportedDocumentTypeError,
    create_default_parser_registry,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("media_type", ["text/plain", " TEXT/PLAIN; charset=utf-8 "])
def test_registry_selects_plain_text_parser_by_media_type(media_type: str) -> None:
    registry = create_default_parser_registry()

    assert isinstance(registry.get_parser(media_type=media_type), PlainTextParser)


def test_registry_selects_markdown_parser_by_media_type() -> None:
    registry = create_default_parser_registry()

    assert isinstance(registry.get_parser(media_type="text/markdown"), MarkdownParser)


@pytest.mark.parametrize(
    "filename",
    [
        "notes.txt",
        "module.py",
        "app.js",
        "view.jsx",
        "types.ts",
        "component.tsx",
        "Main.java",
        "main.go",
    ],
)
def test_registry_uses_plain_text_parser_for_demo_source_files(
    filename: str,
) -> None:
    registry = create_default_parser_registry()

    assert isinstance(registry.get_parser(filename=filename), PlainTextParser)


@pytest.mark.parametrize("filename", ["README.md", "GUIDE.MARKDOWN"])
def test_registry_selects_markdown_parser_by_extension(filename: str) -> None:
    registry = create_default_parser_registry()

    assert isinstance(registry.get_parser(filename=filename), MarkdownParser)


def test_registry_prefers_supported_media_type_over_extension() -> None:
    registry = create_default_parser_registry()

    assert isinstance(
        registry.get_parser(media_type="text/plain", filename="README.md"),
        PlainTextParser,
    )


def test_registry_falls_back_to_extension_for_unknown_media_type() -> None:
    registry = create_default_parser_registry()

    assert isinstance(
        registry.get_parser(
            media_type="application/octet-stream", filename="README.md"
        ),
        MarkdownParser,
    )


def test_registry_rejects_unsupported_document_type() -> None:
    registry = create_default_parser_registry()

    with pytest.raises(UnsupportedDocumentTypeError, match="unsupported document type"):
        registry.get_parser(media_type="application/pdf", filename="report.pdf")
