import re

import pytest

from backend.parsers import TreeSitterParser

pytestmark = pytest.mark.unit

_REQUIRED_METADATA = {
    "path",
    "language",
    "symbol",
    "symbol_type",
    "parent_symbol",
    "start_line",
    "end_line",
}


@pytest.mark.parametrize(
    ("language", "content", "expected_symbols"),
    [
        (
            "python",
            "import os\nclass Greeter:\n    def greet(self):\n        return 'hi'\n\ndef run():\n    return Greeter()\n",
            {
                ("Greeter", "class", ""),
                ("greet", "method", "Greeter"),
                ("run", "function", ""),
            },
        ),
        (
            "javascript",
            "class Greeter { greet() { return 'hi'; } }\nfunction run() { return new Greeter(); }\n",
            {
                ("Greeter", "class", ""),
                ("greet", "method", "Greeter"),
                ("run", "function", ""),
            },
        ),
        (
            "typescript",
            "class Greeter { greet(): string { return 'hi'; } }\nfunction run(): Greeter { return new Greeter(); }\n",
            {
                ("Greeter", "class", ""),
                ("greet", "method", "Greeter"),
                ("run", "function", ""),
            },
        ),
        (
            "tsx",
            "class View { render() { return <main />; } }\nfunction App() { return <View />; }\n",
            {
                ("View", "class", ""),
                ("render", "method", "View"),
                ("App", "function", ""),
            },
        ),
        (
            "java",
            'class Greeter { String greet() { return "hi"; } }\nclass Main { static void run() {} }\n',
            {
                ("Greeter", "class", ""),
                ("greet", "method", "Greeter"),
                ("Main", "class", ""),
                ("run", "method", "Main"),
            },
        ),
        (
            "go",
            'package main\ntype Greeter struct{}\nfunc (g Greeter) Greet() string { return "hi" }\nfunc Run() {}\n',
            {
                ("Greeter", "class", ""),
                ("Greet", "method", "Greeter"),
                ("Run", "function", ""),
            },
        ),
    ],
)
def test_valid_code_is_partitioned_at_semantic_boundaries(
    language: str,
    content: str,
    expected_symbols: set[tuple[str, str, str]],
) -> None:
    parsed = TreeSitterParser(language, "text/x-source").parse(
        content, source_name="src/example.code"
    )

    assert "".join(chunk.content for chunk in parsed.chunks) == content
    actual_symbols = {
        (
            chunk.metadata["symbol"],
            chunk.metadata["symbol_type"],
            chunk.metadata["parent_symbol"],
        )
        for chunk in parsed.chunks
        if chunk.metadata["symbol"]
    }
    assert expected_symbols <= actual_symbols
    for chunk in parsed.chunks:
        assert _REQUIRED_METADATA <= chunk.metadata.keys()
        assert chunk.metadata["path"] == "src/example.code"
        assert all(isinstance(value, str) for value in chunk.metadata.values())
        start_line = int(chunk.metadata["start_line"])
        end_line = int(chunk.metadata["end_line"])
        assert 1 <= start_line <= end_line <= len(content.splitlines())


def test_module_regions_use_documented_empty_symbol_convention() -> None:
    content = "import os\n\ndef answer():\n    return 42\n"

    parsed = TreeSitterParser("python", "text/x-python").parse(
        content, source_name="answer.py"
    )

    module_chunks = [
        chunk for chunk in parsed.chunks if chunk.metadata["symbol_type"] == "module"
    ]
    assert module_chunks
    assert all(chunk.metadata["symbol"] == "" for chunk in module_chunks)
    assert all(chunk.metadata["parent_symbol"] == "" for chunk in module_chunks)


def test_oversized_symbol_is_split_by_token_limit_with_line_provenance() -> None:
    content = "def values():\n    return (\n        one, two, three,\n        four, five, six,\n    )\n"
    parser = TreeSitterParser("python", "text/x-python", token_limit=4)

    first = parser.parse(content, source_name="values.py")
    second = parser.parse(content, source_name="values.py")

    assert first == second
    assert "".join(chunk.content for chunk in first.chunks) == content
    symbol_chunks = [
        chunk for chunk in first.chunks if chunk.metadata["symbol"] == "values"
    ]
    assert len(symbol_chunks) > 1
    assert all(len(re.findall(r"\S+", chunk.content)) <= 4 for chunk in symbol_chunks)
    assert symbol_chunks[0].metadata["start_line"] == "1"
    assert symbol_chunks[-1].metadata["end_line"] == "5"


@pytest.mark.parametrize("token_limit", [0, -1])
def test_token_limit_must_be_positive(token_limit: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        TreeSitterParser("python", "text/x-python", token_limit=token_limit)
