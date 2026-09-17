import pytest

from backend.parsers import MarkdownParser, ParsedChunk, ParsedDocument, PlainTextParser

pytestmark = pytest.mark.unit


def test_plain_text_parser_preserves_content_and_metadata() -> None:
    content = "first line\nsecond line\n"

    parsed = PlainTextParser().parse(content, source_name="notes.txt")

    assert parsed.content == content
    assert parsed.chunks == (
        ParsedChunk(content=content, index=0, metadata={"parser": "plain_text"}),
    )
    assert parsed.metadata == {
        "parser": "plain_text",
        "content_type": "text/plain",
        "source_name": "notes.txt",
    }


def test_plain_text_parser_is_deterministic_for_empty_content() -> None:
    parser = PlainTextParser()

    assert parser.parse("") == parser.parse("")
    assert parser.parse("").chunks[0].content == ""


def test_markdown_parser_preserves_source_and_splits_heading_sections() -> None:
    content = "Intro\n\n# First\nBody\n## Second ##\nMore\n"

    parsed = MarkdownParser().parse(content, source_name="guide.md")

    assert parsed.content == content
    assert [chunk.content for chunk in parsed.chunks] == [
        "Intro\n\n",
        "# First\nBody\n",
        "## Second ##\nMore\n",
    ]
    assert [dict(chunk.metadata) for chunk in parsed.chunks] == [
        {"section": "preamble"},
        {"section": "First", "heading_level": "1"},
        {"section": "Second", "heading_level": "2"},
    ]
    assert parsed.metadata == {
        "parser": "markdown",
        "content_type": "text/markdown",
        "source_name": "guide.md",
    }


def test_markdown_parser_does_not_split_headings_in_fenced_code() -> None:
    content = "# Real\n```md\n# Example\n```\nAfter\n"

    parsed = MarkdownParser().parse(content)

    assert len(parsed.chunks) == 1
    assert parsed.chunks[0].content == content
    assert parsed.chunks[0].metadata == {
        "section": "Real",
        "heading_level": "1",
    }


def test_markdown_parser_does_not_close_fence_with_trailing_text() -> None:
    content = "# Real\n```md\n```not-a-close\n# Still code\n```\n# Next\nBody\n"

    parsed = MarkdownParser().parse(content)

    assert [chunk.content for chunk in parsed.chunks] == [
        "# Real\n```md\n```not-a-close\n# Still code\n```\n",
        "# Next\nBody\n",
    ]
    assert [chunk.metadata["section"] for chunk in parsed.chunks] == [
        "Real",
        "Next",
    ]


def test_markdown_parser_matches_crlf_without_changing_source_content() -> None:
    content = "# A\r\nbody\r\n```md\r\n# hidden\r\n```\r\n# B\r\n"

    parsed = MarkdownParser().parse(content)

    assert parsed.content == content
    assert [chunk.content for chunk in parsed.chunks] == [
        "# A\r\nbody\r\n```md\r\n# hidden\r\n```\r\n",
        "# B\r\n",
    ]
    assert [chunk.metadata["section"] for chunk in parsed.chunks] == ["A", "B"]


def test_markdown_parser_preserves_unspaced_trailing_hash_in_heading() -> None:
    parsed = MarkdownParser().parse("# abc#\n# def ###\n")

    assert [chunk.metadata["section"] for chunk in parsed.chunks] == ["abc#", "def"]


def test_parser_results_are_validated_and_immutable() -> None:
    chunk = ParsedChunk(content="content", index=0, metadata={"kind": "text"})
    document = ParsedDocument(content="content", chunks=(chunk,), metadata={})

    with pytest.raises(TypeError):
        chunk.metadata["kind"] = "changed"  # type: ignore[index]
    with pytest.raises(AttributeError):
        document.content = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="contiguous"):
        ParsedDocument(
            content="content",
            chunks=(ParsedChunk(content="content", index=1),),
        )


def test_parsed_document_copies_chunks_to_an_immutable_tuple() -> None:
    chunks = [ParsedChunk(content="content", index=0)]

    document = ParsedDocument(content="content", chunks=chunks)  # type: ignore[arg-type]
    chunks.append(ParsedChunk(content="later", index=1))

    assert document.chunks == (ParsedChunk(content="content", index=0),)
    with pytest.raises(TypeError, match="ParsedChunk"):
        ParsedDocument(content="content", chunks=["wrong"])  # type: ignore[list-item, arg-type]


@pytest.mark.parametrize("content", [None, b"content", 1])
def test_parsed_chunk_rejects_non_string_content(content: object) -> None:
    with pytest.raises(TypeError, match="chunk content must be a string"):
        ParsedChunk(content=content, index=0)  # type: ignore[arg-type]


@pytest.mark.parametrize("index", [True, False, 1.5, "0"])
def test_parsed_chunk_rejects_non_integer_index(index: object) -> None:
    with pytest.raises(TypeError, match="chunk index must be an integer"):
        ParsedChunk(content="content", index=index)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({1: "value"}, "metadata keys must be strings"),
        ({"key": 1}, "metadata values must be strings"),
    ],
)
def test_parsed_chunk_rejects_non_string_metadata(
    metadata: object,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        ParsedChunk(content="content", index=0, metadata=metadata)  # type: ignore[arg-type]


def test_parsed_document_rejects_non_string_content() -> None:
    with pytest.raises(TypeError, match="document content must be a string"):
        ParsedDocument(content=b"content", chunks=())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({1: "value"}, "metadata keys must be strings"),
        ({"key": 1}, "metadata values must be strings"),
    ],
)
def test_parsed_document_rejects_non_string_metadata(
    metadata: object,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        ParsedDocument(content="content", chunks=(), metadata=metadata)  # type: ignore[arg-type]
