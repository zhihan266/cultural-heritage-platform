"""Storage contract; callers offload synchronous SDK operations to a worker thread."""

from abc import ABC, abstractmethod

from app.modules.rag.domain.vector_models import VectorRecord, VectorSearchHit


class VectorStore(ABC):
    @abstractmethod
    def ensure_collection(self) -> None:
        """Create the configured collection if it is absent."""

    @abstractmethod
    def upsert(self, records: list[VectorRecord]) -> None:
        """Persist records using their stable chunk IDs."""

    @abstractmethod
    def search(
        self, query_vector: list[float], top_k: int = 5,
        document_id: str | None = None,
    ) -> list[VectorSearchHit]:
        """Retrieve dense candidates."""

    @abstractmethod
    def hybrid_search(
        self, *, query_vector: list[float], query_text: str,
        top_k: int = 5, candidate_top_k: int = 20,
        document_id: str | None = None,
    ) -> list[VectorSearchHit]:
        """Fuse dense and keyword candidates; returned score is a fusion score."""

    @abstractmethod
    def delete_by_document(self, document_id: str) -> None:
        """Delete only records belonging to the specified document."""

    @abstractmethod
    def close(self) -> None:
        """Release the storage client's resources."""
