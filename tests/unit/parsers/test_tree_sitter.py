import pytest

from backend.parsers import ParsedChunk, TreeSitterParser

pytestmark = pytest.mark.unit


def test_tree_sitter_parser_marks_valid_source_as_parsed() -> None:
    content = "def answer() -> int:\n    return 42\n"

    parsed = TreeSitterParser("python", "text/x-python").parse(
        content, source_name="answer.py"
    )

    assert parsed.content == content
    assert parsed.chunks == (
        ParsedChunk(
            content=content,
            index=0,
            metadata={
                "parser": "tree_sitter",
                "language": "python",
                "syntax_status": "parsed",
            },
        ),
    )
    assert parsed.metadata == {
        "parser": "tree_sitter",
        "content_type": "text/x-python",
        "language": "python",
        "syntax_status": "parsed",
        "source_name": "answer.py",
    }


def test_tree_sitter_parser_uses_deterministic_syntax_error_fallback() -> None:
    parser = TreeSitterParser("python", "text/x-python")
    content = "def broken(:\n"

    first = parser.parse(content)
    second = parser.parse(content)

    assert first == second
    assert first.content == content
    assert first.chunks[0].content == content
    assert first.metadata == {
        "parser": "tree_sitter",
        "content_type": "text/x-python",
        "language": "python",
        "syntax_status": "syntax_error_fallback",
        "fallback_parser": "plain_text",
    }
    assert first.chunks[0].metadata["syntax_status"] == "syntax_error_fallback"
