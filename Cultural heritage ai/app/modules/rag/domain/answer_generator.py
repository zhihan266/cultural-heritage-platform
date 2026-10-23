"""Generation contract for a registered business AI, without HTTP or ORM dependencies."""

from abc import ABC, abstractmethod


class AnswerGenerator(ABC):
    @abstractmethod
    async def ensure_available(self, ai_id: str) -> None:
        """Validate the selected AI before retrieval, preserving the API's checks."""

    @abstractmethod
    async def generate(
        self, *, ai_id: str, system_prompt: str, user_prompt: str,
    ) -> str:
        """Generate an answer through the selected AI."""
