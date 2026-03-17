"""Base adapter interface and registry."""

from importlib.metadata import entry_points
from pathlib import Path
import re
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
    """Return registry with built-in adapters and optional plugin adapters.

    External adapters can be added via entry points in the
    ``energy_pipeline.adapters`` group. Each entry point should resolve to a
    zero-argument adapter class or factory that returns a ConsumptionAdapter.
    """
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

    _register_plugin_adapters(registry)

    return registry


def _register_plugin_adapters(registry: AdapterRegistry) -> None:
    """Load external adapters from Python entry points.

    Any plugin loading issue is ignored so the default pipeline remains usable
    even when optional adapters are misconfigured.
    """
    try:
        plugin_eps = entry_points(group="energy_pipeline.adapters")
    except TypeError:
        # Backward compatibility with older importlib.metadata behaviour.
        plugin_eps = entry_points().get("energy_pipeline.adapters", [])
    except Exception:
        return

    for ep in plugin_eps:
        try:
            factory = ep.load()
            adapter = factory()
            _ensure_plugin_adapter_name(adapter, ep.name)
            registry.register(adapter)
        except Exception:
            continue


def _ensure_plugin_adapter_name(adapter: ConsumptionAdapter, entry_point_name: str) -> None:
    """Ensure plugin adapters have a meaningful, stable name.

    If a plugin adapter does not define ``name`` (or defines an empty/invalid one),
    derive a readable fallback from the entry-point key.
    """
    current_name = getattr(adapter, "name", None)
    if isinstance(current_name, str) and current_name.strip() and current_name != "unknown":
        return

    normalized = re.sub(r"[^a-z0-9_]+", "_", entry_point_name.lower()).strip("_")
    fallback_name = f"plugin_{normalized or 'adapter'}"
    setattr(adapter, "name", fallback_name)
