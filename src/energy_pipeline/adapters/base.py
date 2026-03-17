"""Base adapter interface and registry."""

from pathlib import Path
from typing import List, Optional, Protocol

import pandas as pd

from ..schema import ConsumptionProfile


class ConsumptionAdapter(Protocol):
    """Protocol for consumption data adapters."""

    name: str

    def detect(self, path: Path) -> bool:
        """Return True if this adapter can handle the file at path."""
        ...

    def parse(self, path: Path) -> pd.DataFrame:
        """Parse the file and return raw DataFrame."""
        ...

    def to_normalized(self, raw: pd.DataFrame) -> ConsumptionProfile:
        """Convert raw DataFrame to normalized ConsumptionProfile."""
        ...


class AdapterRegistry:
    """Registry of adapters with auto-detection."""

    def __init__(self) -> None:
        self._adapters: List[ConsumptionAdapter] = []

    def register(self, adapter: ConsumptionAdapter) -> None:
        """Register an adapter. First registered has priority in detection."""
        self._adapters.append(adapter)

    def detect(self, path: Path) -> Optional[ConsumptionAdapter]:
        """Return the first adapter that can handle the file, or None."""
        path = Path(path)
        if not path.exists():
            return None
        for adapter in self._adapters:
            try:
                if adapter.detect(path):
                    return adapter
            except Exception:
                continue
        return None

    def get(self, name: str) -> Optional[ConsumptionAdapter]:
        """Get adapter by name."""
        for adapter in self._adapters:
            if getattr(adapter, "name", "") == name:
                return adapter
        return None

    def list_names(self) -> List[str]:
        """List registered adapter names."""
        return [getattr(a, "name", "unknown") for a in self._adapters]


# Default registry with built-in adapters
def get_default_registry() -> AdapterRegistry:
    """Return registry with all built-in adapters registered."""
    from .adams_meetdata import AdamsMeetdataAdapter
    from .belgian_dso import BelgianDSOAdapter
    from .historiek_dagtotalen import HistoriekDagtotalenAdapter
    from .pvgis_timeseries import PvgisTimeseriesAdapter
    from .smulders_offtake import SmuldersOfftakeAdapter

    registry = AdapterRegistry()
    registry.register(HistoriekDagtotalenAdapter())  # CSV before Excel for detection
    registry.register(PvgisTimeseriesAdapter())  # PVGIS CSV (time, P) - before other CSVs in pv/
    registry.register(AdamsMeetdataAdapter())  # Adams meetdata before generic Excel
    registry.register(SmuldersOfftakeAdapter())  # Project folder offtake (Time Series sheet)
    registry.register(BelgianDSOAdapter())
    return registry
