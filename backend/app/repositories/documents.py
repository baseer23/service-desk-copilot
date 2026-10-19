"""Persistent local knowledge staging, separate from verified semantic vectors."""

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from backend.app.services.chunking import split_text
from backend.app.store.vector_chroma import StorageError


def query_terms(question: str) -> list[str]:
    stopwords = {
        "a", "an", "and", "according", "about", "are", "as", "at", "be", "by",
        "did", "do", "does", "for", "from", "how", "i", "in", "is", "it", "me",
        "my", "of", "on", "only", "or", "say", "says", "that", "the", "this", "to",
        "was", "were", "what", "when", "where", "which", "who", "with",
    }
    return list(
        dict.fromkeys(
            term for term in re.findall(r"[\w-]+", question.casefold())
            if len(term) > 1 and term not in stopwords
        )
    )[:16]


def lexical_score(question: str, text: str, metadata: dict[str, Any] | None = None) -> int:
    """Rank actual lexical evidence and explicit source-type intent."""
    terms = query_terms(question)
    folded = text.casefold()
    words = set(re.findall(r"[\w-]+", folded))
    score = sum(2 for term in terms if term in words)
    # Adjacent content terms are much stronger than isolated generic matches.
    score += sum(
        3 for left, right in zip(terms, terms[1:])
        if re.search(rf"(?<!\w){re.escape(left)}\W+{re.escape(right)}(?!\w)", folded)
    )
    title = str((metadata or {}).get("title") or "").casefold()
    publication_query = {"article", "headline", "newspaper", "paper", "front-page", "frontpage"}
    publication_title = ("newspaper", "e paper", "e-paper", "epaper")
    publication_intent = bool(publication_query.intersection(terms)) or {"front", "page"}.issubset(terms)
    if publication_intent and any(value in title for value in publication_title):
        score += 8
    score += sum(2 for term in terms if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", title))
    return score


def explicit_publication_source(question: str) -> bool:
    folded = " ".join(question.casefold().split())
    return any(
        phrase in folded
        for phrase in ("this newspaper", "my newspaper", "uploaded newspaper", "the paper")
    )


def is_publication(metadata: dict[str, Any] | None) -> bool:
    title = str((metadata or {}).get("title") or "").casefold()
    return any(value in title for value in ("newspaper", "e paper", "e-paper", "epaper"))


class DocumentRepository:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS knowledge_documents "
                "(id TEXT PRIMARY KEY,title TEXT NOT NULL,text TEXT NOT NULL,"
                "source_type TEXT NOT NULL,pages INTEGER NOT NULL,"
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS knowledge_chunks "
                "(id TEXT PRIMARY KEY,doc_id TEXT NOT NULL,text TEXT NOT NULL)"
            )
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_search USING fts5(id UNINDEXED,text)"
            )
            yield db
            db.commit()
        except (sqlite3.Error, OSError):
            if db:
                db.rollback()
            raise StorageError(
                "Local document store unavailable; existing knowledge preserved"
            ) from None
        except BaseException:
            if db:
                db.rollback()
            raise
        finally:
            if db:
                db.close()

    def save(
        self, text: str, title: str | None, source_type: str, pages: int = 1
    ) -> dict[str, Any]:
        if not text.strip() or len(text) > 1_000_000:
            raise ValueError("Document text must contain 1 to one million characters")
        digest = hashlib.sha256(text.encode()).hexdigest()
        chunks = split_text(text)
        with self.transaction() as db:
            exists = db.execute(
                "SELECT 1 FROM knowledge_documents WHERE id=?", (digest,)
            ).fetchone()
            if not exists:
                db.execute(
                    "INSERT INTO knowledge_documents(id,title,text,source_type,pages) "
                    "VALUES(?,?,?,?,?)",
                    (digest, title or "Untitled", text, source_type, pages),
                )
                for chunk in chunks:
                    identifier = f"{digest}-{chunk['ord']}"
                    db.execute(
                        "INSERT INTO knowledge_chunks VALUES(?,?,?)",
                        (identifier, digest, chunk["text"]),
                    )
                    db.execute(
                        "INSERT INTO knowledge_search VALUES(?,?)", (identifier, chunk["text"])
                    )
        return {
            "id": digest,
            "chunks": len(chunks),
            "duplicate": bool(exists),
            "storage": "local",
            "indexed": False,
        }

    def get(self, identifier: str) -> dict[str, Any]:
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM knowledge_documents WHERE id=?", (identifier,)
            ).fetchone()
            if not row:
                raise ValueError("Document not found")
            return dict(row)

    def list_documents(self) -> list[dict[str, Any]]:
        with self.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,title,source_type,pages,created_at FROM knowledge_documents "
                    "ORDER BY created_at DESC,id LIMIT 100"
                )
            ]

    def search(self, question: str, top_k: int) -> list[dict[str, Any]]:
        terms = query_terms(question)
        if not terms:
            return []
        query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        with self.transaction() as db:
            rows = db.execute(
                "SELECT c.id,c.text,d.id AS doc_id,d.title,d.source_type,d.created_at,"
                "bm25(knowledge_search) AS score "
                "FROM knowledge_search JOIN knowledge_chunks c ON c.id=knowledge_search.id "
                "JOIN knowledge_documents d ON d.id=c.doc_id WHERE knowledge_search MATCH ? "
                "ORDER BY score,c.id LIMIT ?",
                (query, top_k),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "text": row["text"],
                    "score": row["score"],
                    "match_type": "keyword",
                    "metadata": {
                        k: row[k] for k in ("doc_id", "title", "source_type", "created_at")
                    },
                    "storage": "local",
                }
                for row in rows
            ]
