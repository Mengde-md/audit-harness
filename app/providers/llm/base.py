from abc import ABC, abstractmethod


class LlmProvider(ABC):
    @abstractmethod
    async def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        raise NotImplementedError

