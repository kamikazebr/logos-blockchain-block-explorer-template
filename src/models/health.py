from typing import Optional

from core.models import NbeSchema


class Health(NbeSchema):
    healthy: bool
    # Node API generation detected by the backend, if any.
    node_api: Optional[str] = None

    def __str__(self):
        return "Healthy" if self.healthy else "Unhealthy"

    def __repr__(self):
        return f"<Health(healthy={self.healthy}, node_api={self.node_api})>"
