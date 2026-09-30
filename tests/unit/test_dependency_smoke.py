"""Public API compatibility checks with wholly synthetic, in-memory fixtures."""

import os
from io import BytesIO, StringIO

import httpx
import pytest
from dotenv import dotenv_values
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader, PdfWriter


def test_langchain_core_document_message_and_prompt() -> None:
    document = Document(
        page_content="Synthetic fixture text, not company evidence.",
        metadata={"source": "synthetic", "page": 1},
    )
    message = HumanMessage(content=document.page_content)

    assert message.type == "human"
    assert message.content == document.page_content
    assert document.metadata == {"source": "synthetic", "page": 1}

    prompt = ChatPromptTemplate.from_messages([("human", "Fixture: {text}")])
    messages = prompt.invoke({"text": document.page_content}).to_messages()

    assert len(messages) == 1
    assert messages[0].type == "human"
    assert (
        messages[0].content == "Fixture: Synthetic fixture text, not company evidence."
    )


def test_text_splitter_with_test_only_settings() -> None:
    # These settings are only for this fixture, not a production chunk policy.
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=16,
        chunk_overlap=0,
        separators=[" "],
        length_function=len,
    )
    document = Document(
        page_content="Synthetic alpha beta gamma delta epsilon",
        metadata={"source": "synthetic"},
    )

    chunks = splitter.split_documents([document])

    assert len(chunks) > 1
    assert all(0 < len(chunk.page_content) <= 16 for chunk in chunks)
    assert " ".join(chunk.page_content for chunk in chunks) == document.page_content
    assert all(chunk.metadata == document.metadata for chunk in chunks)


def test_httpx_mock_transport_without_sockets() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://fixture.invalid/synthetic"
        return httpx.Response(200, json={"source": "synthetic", "ok": True})

    transport = httpx.MockTransport(respond)
    with httpx.Client(transport=transport, trust_env=False) as client:
        response = client.get("https://fixture.invalid/synthetic")

    assert response.status_code == 200
    assert response.json() == {"source": "synthetic", "ok": True}


def test_dotenv_stream_does_not_mutate_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        os,
        "environ",
        {"SYNTHETIC_SENTINEL": "fake-sentinel", "SYNTHETIC_LABEL": "fake-existing"},
    )
    # Only fake literal values; no file discovery, interpolation, or load_dotenv.
    values = dotenv_values(
        stream=StringIO("SYNTHETIC_LABEL=fake-fixture\nSYNTHETIC_COUNT=2\n"),
        interpolate=False,
    )

    assert values == {"SYNTHETIC_LABEL": "fake-fixture", "SYNTHETIC_COUNT": "2"}
    assert os.environ == {
        "SYNTHETIC_SENTINEL": "fake-sentinel",
        "SYNTHETIC_LABEL": "fake-existing",
    }


def test_pypdf_synthetic_blank_page_roundtrip() -> None:
    # A blank in-memory fixture, not an investment report or rendered output.
    with BytesIO() as stream, PdfWriter() as writer:
        writer.add_blank_page(width=72, height=144)
        writer.add_metadata({"/Title": "Synthetic compatibility fixture"})
        writer.write(stream)
        stream.seek(0)
        reader = PdfReader(stream)

        assert len(reader.pages) == 1
        page = reader.pages[0]
        assert float(page.mediabox.width) == 72
        assert float(page.mediabox.height) == 144
        assert page.rotation == 0
        assert reader.metadata.title == "Synthetic compatibility fixture"
