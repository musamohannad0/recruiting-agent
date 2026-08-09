from __future__ import annotations

from ..models import AtsType
from .ashby import AshbyConnector
from .greenhouse import GreenhouseConnector
from .lever import LeverConnector

CONNECTORS = {
    AtsType.greenhouse: GreenhouseConnector(),
    AtsType.lever: LeverConnector(),
    AtsType.ashby: AshbyConnector(),
}
