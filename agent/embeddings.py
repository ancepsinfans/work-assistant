"""
Local semantic search over the permanent decision/experiment logs.

Uses a local sentence-transformers model (fully offline, no API gateway required).
embeddings endpoint. Storage is a small SQLite table with
brute-force cosine similarity in numpy — appropriate at the scale of a personal
decision/experiment log (hundreds to low thousands of entries), not the tens of
thousands+ where a dedicated vector database starts earning its complexity.
"""

# Model is already downloaded and cached locally (confirmed working offline-capable
# this session). Without this, sentence-transformers hits the HF Hub on every load
# just to check for cache freshness — a real network dependency reintroduced right
# where the whole point was removing one, and one that gets hit every few minutes
# once meeting_brief.py's frequent poller is running. If MODEL_NAME below is ever
# changed to something not yet cached, unset this once to let it download fresh.
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import logging
import sqlite3
from datetime import datetime

import numpy as np

import timeutil

logger = logging.getLogger(__name__)

MODEL_NAME = "all-mpnet-base-v2"

_model = None  # lazy-loaded once per process


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading local embedding model (%s)...", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(texts: list[str]) -> np.ndarray:
    """Embed a batch of texts. Returns an (n, dim) float32 array, L2-normalized
    so cosine similarity reduces to a plain dot product."""
    model = _get_model()
    vecs = model.encode(list(texts), normalize_embeddings=True)
    return np.asarray(vecs, dtype=np.float32)


class EmbeddingStore:
    def __init__(self, db_path: str):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    text TEXT NOT NULL,
                    date TEXT,
                    embedding BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (id, source)
                )
                """
            )

    def upsert_many(self, items: list[dict]) -> int:
        """items: [{"id", "source", "text", "date"}, ...]. One batched embed() call.
        Returns the number of items embedded."""
        if not items:
            return 0
        vecs = embed([it["text"] for it in items])
        now = timeutil.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings (id, source, text, date, embedding, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (it["id"], it["source"], it["text"], it.get("date", ""), vec.tobytes(), now)
                    for it, vec in zip(items, vecs)
                ],
            )
        logger.info("Embedded %d entries", len(items))
        return len(items)

    def upsert(self, id: str, source: str, text: str, date: str = "") -> None:
        self.upsert_many([{"id": id, "source": source, "text": text, "date": date}])

    def search(self, query: str, k: int = 5, source: str | None = None) -> list[dict]:
        """Semantic search. Returns up to k [{"id","source","text","date","score"}],
        best match first. Empty list if the store has nothing indexed yet."""
        with sqlite3.connect(self.db_path) as conn:
            if source:
                rows = conn.execute(
                    "SELECT id, source, text, date, embedding FROM embeddings WHERE source = ?",
                    (source,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, source, text, date, embedding FROM embeddings"
                ).fetchall()

        if not rows:
            return []

        matrix = np.frombuffer(b"".join(r[4] for r in rows), dtype=np.float32).reshape(
            len(rows), -1
        )
        query_vec = embed([query])[0]
        scores = matrix @ query_vec

        order = np.argsort(-scores)[:k]
        return [
            {
                "id": rows[i][0],
                "source": rows[i][1],
                "text": rows[i][2],
                "date": rows[i][3],
                "score": float(scores[i]),
            }
            for i in order
        ]

    def count(self, source: str | None = None) -> int:
        with sqlite3.connect(self.db_path) as conn:
            if source:
                return conn.execute(
                    "SELECT COUNT(*) FROM embeddings WHERE source=?", (source,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
