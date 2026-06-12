from typing import Any, Optional, Self

from core.models import NbeSerializer
from models.health import Health


class HealthSerializer(NbeSerializer):
    is_healthy: bool
    # Which node API generation was detected (e.g. "modern", "legacy (<= 0.1.2)").
    # None when not yet detected or not applicable (e.g. fake node API).
    node_api: Optional[str] = None

    def into_health(self) -> Health:
        return Health.model_validate({"healthy": self.is_healthy, "node_api": self.node_api})

    @classmethod
    def from_healthy(cls, node_api: Optional[str] = None) -> Self:
        return cls.model_validate({"is_healthy": True, "node_api": node_api})

    @classmethod
    def from_unhealthy(cls, node_api: Optional[str] = None) -> Self:
        return cls.model_validate({"is_healthy": False, "node_api": node_api})
