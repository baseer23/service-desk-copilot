from abc import ABC, abstractmethod


class LocalModelProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        raise NotImplementedError

