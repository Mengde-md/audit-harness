from abc import ABC, abstractmethod


class EvidenceParser(ABC):
    @abstractmethod
    def parse(self, step_name: str, stdout: str, stderr: str) -> list[dict]:
        raise NotImplementedError

