"""Consumption data adapters for various energy provider formats."""

from .adams_meetdata import AdamsMeetdataAdapter
from .base import AdapterRegistry, ConsumptionAdapter
from .belgian_dso import BelgianDSOAdapter
from .historiek_dagtotalen import HistoriekDagtotalenAdapter
from .smulders_offtake import SmuldersOfftakeAdapter

__all__ = [
    "AdamsMeetdataAdapter",
    "AdapterRegistry",
    "BelgianDSOAdapter",
    "ConsumptionAdapter",
    "HistoriekDagtotalenAdapter",
    "SmuldersOfftakeAdapter",
]
