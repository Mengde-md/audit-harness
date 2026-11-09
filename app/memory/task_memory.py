import json
from pathlib import Path
from typing import Any

from app.memory.constants import MemoryScope, MemorySource, TrustLevel
from app.utils.id_generator import generate_id
from app.utils.time import utc_now


class TaskMemoryWriter:
    def __init__(self, job_root: Path):
        self.path = job_root / "execution-events.jsonl"

    def append(self, event: str, **data: Any) -> None:
        timestamp = utc_now().isoformat()
        payload = {
            # Keep these two fields at the top level for existing timeline tools.
            "timestamp": timestamp,
            "event": event,
            "memory_id": generate_id(),
            "scope": MemoryScope.SINGLE_AUDIT.value,
            "source": MemorySource.SYSTEM.value,
            "trust_level": TrustLevel.LOW.value,
            "created_at": timestamp,
            "content": {"event": event, **data},
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, default=str))
            stream.write("\n")
