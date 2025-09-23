import os

import pytest

from backend.app.services.chunking import approx_tokens, split_text


def test_approx_tokens_deterministic():
    assert approx_tokens("hello") == approx_tokens("hello")
    assert approx_tokens("hello world") >= 1


def test_split_text_respects_chunk_tokens(monkeypatch):
    monkeypatch.setenv("CHUNK_TOKENS", "4")
    monkeypatch.setenv("CHUNK_OVERLAP", "1")
    text = "one two three four five six seven"
    chunks = split_text(text)
    assert len(chunks) == 2
    assert all(chunk["tokens"] <= 4 for chunk in chunks)
    assert chunks[0]["text"].startswith("one")
    assert chunks[1]["text"].split()[0] == "four"


def test_split_text_handles_short_input(monkeypatch):
    monkeypatch.delenv("CHUNK_TOKENS", raising=False)
    monkeypatch.delenv("CHUNK_OVERLAP", raising=False)
    chunks = split_text("short text")
    assert len(chunks) == 1
    assert chunks[0]["tokens"] == approx_tokens("short text")
