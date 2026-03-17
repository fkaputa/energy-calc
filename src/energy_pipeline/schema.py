"""Normalized consumption schema and configuration."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import pandas as pd


@dataclass
class ConsumptionProfile:
    """
    Normalized electricity consumption profile.

    - timestamp: start of each interval (index)
    - power_kw: average power in kW over the interval
    - interval_minutes: duration of each interval
    - source_identifier: meter/source ID (e.g. EAN-code)
    """

    data: pd.DataFrame
    source_identifier: str = ""
    interval_minutes: int = 15

    def __post_init__(self) -> None:
        if not isinstance(self.data, pd.DataFrame):
            raise TypeError("data must be a pandas DataFrame")
        if "power_kw" not in self.data.columns:
            raise ValueError("data must have 'power_kw' column")
        has_ts = "timestamp" in self.data.columns
        has_dt_idx = isinstance(self.data.index, pd.DatetimeIndex)
        if not has_ts and not has_dt_idx:
            raise ValueError("data must have 'timestamp' column or DatetimeIndex")

    @property
    def _power_series(self) -> pd.Series:
        """Power in kW as Series with datetime index."""
        df = self.data
        if "timestamp" in df.columns:
            return df.set_index("timestamp")["power_kw"]
        return df["power_kw"]

    @property
    def power_kw(self) -> pd.Series:
        """Power in kW, indexed by timestamp."""
        return self._power_series

    @property
    def power_mw(self) -> pd.Series:
        """Power in MW for PyPSA (positive = demand)."""
        return self._power_series / 1000.0

    @property
    def timestamps(self) -> pd.DatetimeIndex:
        """Datetime index."""
        return pd.DatetimeIndex(self._power_series.index)

    def to_series(self) -> pd.Series:
        """Return power as Series in MW with datetime index (for PyPSA loads-p_set)."""
        return self.power_mw.copy()

    @classmethod
    def from_series(
        cls,
        power_kw: pd.Series,
        source_identifier: str = "",
        interval_minutes: int = 15,
    ) -> "ConsumptionProfile":
        """Create from a pandas Series (index=datetime, values=power_kw)."""
        df = pd.DataFrame({"timestamp": power_kw.index, "power_kw": power_kw.values})
        return cls(data=df, source_identifier=source_identifier, interval_minutes=interval_minutes)

    @classmethod
    def from_csv(
        cls,
        path: Union[str, Path],
        source_identifier: str = "",
        interval_minutes: int = 15,
    ) -> "ConsumptionProfile":
        """Create from a normalized CSV (columns: timestamp, power_kw)."""
        df = pd.read_csv(Path(path), parse_dates=["timestamp"])
        if "power_kw" not in df.columns:
            raise ValueError("CSV must have a 'power_kw' column")
        return cls(data=df, source_identifier=source_identifier, interval_minutes=interval_minutes)


@dataclass
class NormalizedConfig:
    """Configuration for normalization and simulation."""

    # Resampling
    resample_rule: str = "15min"  # pandas resample rule: 15min, 1H, etc.

    # Time-of-use pricing (hour of day, 0-23)
    peak_start_hour: int = 7
    peak_end_hour: int = 21  # exclusive, so peak = 7-20
    peak_price_eur_mwh: float = 150.0
    off_peak_price_eur_mwh: float = 80.0

    # Battery
    battery_round_trip_efficiency: float = 0.9
    battery_max_hours: float = 4.0
    battery_p_nom_max_mw: Optional[float] = None  # cap on battery power (MW); None = no cap
    battery_capital_cost_eur_kwh: float = 150.0
    battery_inverter_cost_eur_kw: float = 170.0
    battery_lifetime_years: float = 25.0
    discount_rate: float = 0.05

    # Grid
    grid_p_nom_mw: float = 1000.0  # sufficiently large
