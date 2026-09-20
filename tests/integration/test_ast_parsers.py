import pytest

from backend.parsers import TreeSitterParser, create_default_parser_registry

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("filename", "media_type", "language", "content"),
    [
        ("module.py", "text/x-python", "python", "def f():\n    return 1\n"),
        ("app.js", "text/javascript", "javascript", "function f() { return 1; }"),
        ("types.ts", "text/typescript", "typescript", "const x: number = 1;"),
        ("view.tsx", "text/tsx", "tsx", "const view = <div>Hello</div>;"),
        ("Main.java", "text/x-java-source", "java", "class Main {}"),
        ("main.go", "text/x-go", "go", "package main\nfunc main() {}\n"),
    ],
)
def test_registry_parses_supported_source_with_tree_sitter(
    filename: str,
    media_type: str,
    language: str,
    content: str,
) -> None:
    parser = create_default_parser_registry().get_parser(
        media_type=media_type, filename=filename
    )

    parsed = parser.parse(content, source_name=filename)

    assert isinstance(parser, TreeSitterParser)
    assert parser.language == language
    assert parsed.content == content
    assert parsed.metadata["syntax_status"] == "parsed"
    assert parsed.metadata["language"] == language
    assert parsed.chunks[0].content == content
