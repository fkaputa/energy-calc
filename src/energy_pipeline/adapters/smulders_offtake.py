"""Adapter for Smulders/DSO offtake Excel: one sheet 'Time Series', Date + Time + kWh per quarter-hour per connection."""

import re
from pathlib import Path

import pandas as pd

from ..schema import ConsumptionProfile

# Filename often contains EAN (18 digits), e.g. "541448860023115433_SC_OFFTAKE (2025).xlsx"
EAN_IN_FILENAME = re.compile(r"(\d{18})")
SHEET_NAME = "Time Series"


class SmuldersOfftakeAdapter:
    """Adapter for Excel files with sheet 'Time Series': Date, Time, and one column with kWh per 15 min.

    One file = one aansluiting (one connection). Values are energy in kWh per quarter-hour;
    we convert to power_kw = kWh / 0.25.
    """

    name = "smulders_offtake"

    def detect(self, path: Path) -> bool:
        """Detect by: .xlsx and presence of sheet 'Time Series' with expected structure."""
        path = Path(path)
        if path.suffix.lower() != ".xlsx":
            return False
        try:
            xl = pd.ExcelFile(path, engine="openpyxl")
            if SHEET_NAME not in xl.sheet_names:
                return False
            df = pd.read_excel(path, sheet_name=SHEET_NAME, header=None, nrows=5)
            if df.shape[0] < 2 or df.shape[1] < 3:
                return False
            # First row: headers; second column should be Time, third numeric
            first_row = df.iloc[0].astype(str).str.lower()
            if "time" not in first_row.iloc[1]:
                return False
            return True
        except Exception:
            return False

    def parse(self, path: Path) -> pd.DataFrame:
        """Parse 'Time Series' sheet; return DataFrame with timestamp and power_kw."""
        path = Path(path)
        df = pd.read_excel(path, sheet_name=SHEET_NAME, header=None, engine="openpyxl")
        if df.shape[0] < 2 or df.shape[1] < 3:
            raise ValueError(f"Sheet '{SHEET_NAME}' has insufficient rows/columns")

        # Row 0: Date, Time, and one or more kWh columns (e.g. "541448860023115433_SC_OFFTAKE_ACT_BR4 [kWh]")
        date_col = 0
        time_col = 1
        # Use all columns from index 2 as energy (kWh) and sum them (some files may have multiple meters)
        value_cols = list(range(2, df.shape[1]))

        data = df.iloc[1:].copy()
        data.columns = range(data.shape[1])

        # Build timestamp: Date + Time
        date_ser = pd.to_datetime(data[date_col], errors="coerce")
        time_ser = data[time_col].astype(str).str.strip()
        # Time can be "00:00", "00:15" or "00:00:00"
        dt_str = date_ser.dt.strftime("%Y-%m-%d") + " " + time_ser
        data["timestamp"] = pd.to_datetime(dt_str, errors="coerce")
        data = data.dropna(subset=["timestamp"])

        # Sum all energy columns (kWh per 15 min) -> power_kw = kWh / 0.25
        energy_kwh = pd.Series(0.0, index=data.index)
        for c in value_cols:
            energy_kwh = energy_kwh + pd.to_numeric(data[c], errors="coerce").fillna(0)
        data["power_kw"] = energy_kwh / 0.25  # 15 min interval

        out = data[["timestamp", "power_kw"]].drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        return out

    def to_normalized(self, raw: pd.DataFrame) -> ConsumptionProfile:
        """Build ConsumptionProfile. Optionally set source_identifier from path (EAN from filename)."""
        if raw.empty:
            raise ValueError("No data to normalize")
        path = getattr(raw, "attrs", {}).get("path")
        ean = ""
        if path is not None:
            m = EAN_IN_FILENAME.search(Path(path).name)
            if m:
                ean = m.group(1)
        return ConsumptionProfile(
            data=raw[["timestamp", "power_kw"]].copy(),
            source_identifier=ean,
            interval_minutes=15,
        )
