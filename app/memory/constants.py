"""Shared memory scope, source, and trust-level constants."""

from enum import StrEnum


class MemoryScope(StrEnum):
    """Visibility boundary for one memory record."""

    SINGLE_AUDIT = "single_audit"
    SINGLE_REPO = "single_repo"
    CROSS_REPO = "cross_repo"


class MemorySource(StrEnum):
    """Actor that created a memory record."""

    HUMAN = "human"
    SYSTEM = "system"


class TrustLevel(StrEnum):
    """Evidence strength, independent from the memory consumer policy."""

    MAX = "MAX"
    HIGH = "HIGH"
    MIDDLE = "MIDDLE"
    LOW = "LOW"


TRUST_RANK = {
    TrustLevel.MAX: 4,
    TrustLevel.HIGH: 3,
    TrustLevel.MIDDLE: 2,
    TrustLevel.LOW: 1,
}
