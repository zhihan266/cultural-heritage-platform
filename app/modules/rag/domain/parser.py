from abc import ABC, abstractmethod
from pathlib import Path

from app.modules.rag.domain.models import RagDocument


class DocumentParser(ABC):

    @abstractmethod
    def parse(self, file_path: Path) -> list[RagDocument]:
        pass
