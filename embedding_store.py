"""
embedding_store.py - RAG embedding storage and retrieval.

Uses Ollama's /api/embed endpoint to generate text embeddings,
stores them in a pickle file, and performs cosine similarity search
to retrieve relevant past conversations.
"""

import logging
import os
import pickle
import time

import numpy as np
import requests

logger = logging.getLogger("openpaw.embeddings")


class EmbeddingStore:
    def __init__(
        self,
        data_dir: str,
        ollama_base_url: str = "http://localhost:11434",
        embed_model: str = "nomic-embed-text",
        top_k: int = 5,
        similarity_threshold: float = 0.3,
    ):
        self.data_dir = data_dir
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.embed_model = embed_model
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold

        self._store_path = os.path.join(data_dir, "embeddings.pkl")
        self._entries: list[dict] = []
        self._embeddings: np.ndarray | None = None  # shape (N, dim)

        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(self._store_path):
            try:
                with open(self._store_path, "rb") as f:
                    data = pickle.load(f)
                self._entries = data.get("entries", [])
                self._embeddings = data.get("embeddings", None)
                logger.info(
                    "Loaded %d embeddings from %s", len(self._entries), self._store_path
                )
            except Exception as exc:
                logger.warning("Failed to load embeddings – starting fresh: %s", exc)
                self._entries = []
                self._embeddings = None

    def _save(self) -> None:
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self._store_path, "wb") as f:
                pickle.dump(
                    {"entries": self._entries, "embeddings": self._embeddings}, f
                )
        except Exception as exc:
            logger.error("Failed to save embeddings: %s", exc)

    # ------------------------------------------------------------------
    # Ollama embedding API
    # ------------------------------------------------------------------
    def embed_text(self, text: str) -> np.ndarray | None:
        """Generate an embedding vector for *text* using Ollama."""
        try:
            resp = requests.post(
                f"{self.ollama_base_url}/api/embed",
                json={"model": self.embed_model, "input": text},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings")
            if embeddings and len(embeddings) > 0:
                return np.array(embeddings[0], dtype=np.float32)
            logger.warning("Empty embedding response: %s", data)
            return None
        except requests.ConnectionError:
            logger.warning("Cannot connect to Ollama for embedding")
            return None
        except requests.Timeout:
            logger.warning("Embedding request timed out")
            return None
        except requests.HTTPError as exc:
            logger.warning("Embedding HTTP error: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Unexpected embedding error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Store entries
    # ------------------------------------------------------------------
    def add_entry(
        self, user_id: int, role: str, content: str, timestamp: float
    ) -> None:
        """Embed *content* and add it to the store."""
        embedding = self.embed_text(content)
        if embedding is None:
            return

        self._entries.append(
            {
                "user_id": user_id,
                "role": role,
                "content": content,
                "timestamp": timestamp,
            }
        )

        if self._embeddings is None:
            self._embeddings = embedding.reshape(1, -1)
        else:
            self._embeddings = np.vstack([self._embeddings, embedding.reshape(1, -1)])

        self._save()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int | None = None,
        user_id: int | None = None,
    ) -> list[dict]:
        """Return the top-k most similar entries to *query_embedding*."""
        if self._embeddings is None or len(self._entries) == 0:
            return []

        k = top_k or self.top_k

        norms = np.linalg.norm(self._embeddings, axis=1)
        query_norm = np.linalg.norm(query_embedding)
        if query_norm == 0:
            return []

        similarities = (self._embeddings @ query_embedding) / (norms * query_norm)

        candidates = []
        for i, sim in enumerate(similarities):
            if sim < self.similarity_threshold:
                continue
            entry = self._entries[i]
            if user_id is not None and entry["user_id"] != user_id:
                continue
            candidates.append((i, float(sim)))

        candidates.sort(key=lambda x: x[1], reverse=True)
        candidates = candidates[:k]

        return [{**self._entries[idx], "similarity": sim} for idx, sim in candidates]

    def search_by_text(self, text: str, **kwargs) -> list[dict]:
        """Embed *text* and search for similar entries."""
        embedding = self.embed_text(text)
        if embedding is None:
            return []
        return self.search(embedding, **kwargs)

    # ------------------------------------------------------------------
    # Backfill existing history
    # ------------------------------------------------------------------
    def backfill_from_history(self, history: dict[str, list[dict]]) -> int:
        """Import existing conversation history into the embedding store.

        Returns the number of entries added.
        """
        existing = {(e["content"], e["timestamp"]) for e in self._entries}
        count = 0

        for user_id_str, messages in history.items():
            try:
                user_id = int(user_id_str)
            except ValueError:
                continue
            for msg in messages:
                content = msg.get("content", "")
                timestamp = msg.get("timestamp", time.time())
                if not content or (content, timestamp) in existing:
                    continue
                role = msg.get("role", "user")
                self.add_entry(user_id, role, content, timestamp)
                count += 1

        return count

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    def get_entry_count(self) -> int:
        return len(self._entries)

    def clear(self, user_id: int | None = None) -> None:
        """Clear embeddings. If *user_id* given, clear only that user's entries."""
        if user_id is None:
            self._entries = []
            self._embeddings = None
        else:
            keep_indices = [
                i for i, e in enumerate(self._entries) if e["user_id"] != user_id
            ]
            self._entries = [self._entries[i] for i in keep_indices]
            if self._embeddings is not None and keep_indices:
                self._embeddings = self._embeddings[keep_indices]
            else:
                self._embeddings = None
        self._save()
        logger.info("Cleared embeddings (user_id=%s)", user_id)
