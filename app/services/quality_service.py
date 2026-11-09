"""Compatibility service for the Quality Intelligence Engine."""

from typing import Any

from app.quality import QualityEngine


class QualityService:
    """Keep the historical service API while delegating to QualityEngine."""

    def __init__(self) -> None:
        """Create the quality engine used by report generation."""
        self.engine = QualityEngine()

    def build(self, report: dict[str, Any]) -> dict[str, Any]:
        """Build quality intelligence plus legacy score fields."""
        return self.engine.build(report)
