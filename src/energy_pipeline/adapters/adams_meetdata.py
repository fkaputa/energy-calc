"""Adapter for Adams meetdata Excel exports (multiple sheets, 2 electricity meters)."""

import re
from pathlib import Path

import pandas as pd

from ..schema import ConsumptionProfile


# Filename contains "Adams meetdata" and ends with .xlsx
# Sheets: "Metering data 2022", "Metering data 2023", "Metering data 2024"
METERING_SHEET_PATTERN = re.compile(r"Metering data 20\d{2}", re.IGNORECASE)


class AdamsMeetdataAdapter:
    """Adapter for Adams meetdata Excel files.

    Excel has one sheet per year (e.g. "Metering data 2022"). Each sheet:
    - Row 0: MEC, then meter IDs (BE.5414492000...)
    - Row 1: EDI, ...
    - Row 2: UNIT, kW, kW
    - Row 3: TGRID, 15, 15  (15 min interval)
    - From row 4: col 0 = timestamp, col 1 = meter 1 power (kW), col 2 = meter 2 power (kW)

    The two electricity meters are summed before producing the normalized profile.
    """

    name = "adams_meetdata"

    def detect(self, path: Path) -> bool:
        """Detect by filename and sheet names."""
        path = Path(path)
        if path.suffix.lower() != ".xlsx":
            return False
        name = path.name
        if "Adams" not in name or "meetdata" not in name:
            return False
        try:
            xl = pd.ExcelFile(path, engine="openpyxl")
            for sheet in xl.sheet_names:
                if METERING_SHEET_PATTERN.match(sheet.strip()):
                    return True
        except Exception:
            pass
        return False

    def parse(self, path: Path) -> pd.DataFrame:
        """Parse all 'Metering data 20XX' sheets; return DataFrame with timestamp and two meter columns."""
        path = Path(path)
        xl = pd.ExcelFile(path, engine="openpyxl")
        sheets_to_read = [s for s in xl.sheet_names if METERING_SHEET_PATTERN.match(s.strip())]
        if not sheets_to_read:
            raise ValueError("No 'Metering data 20XX' sheets found in Excel file")

        frames = []
        for sheet_name in sheets_to_read:
            df = pd.read_excel(path, sheet_name=sheet_name, header=None, engine="openpyxl")
            if df.shape[0] <= 4 or df.shape[1] < 3:
                continue
            # Data from row 4: col 0 = timestamp, col 1 and 2 = power kW
            data = df.iloc[4:].copy()
            data.columns = ["timestamp", "power_meter1_kw", "power_meter2_kw"]
            data = data[["timestamp", "power_meter1_kw", "power_meter2_kw"]]
            # Parse timestamp (format can be "01.01.2022 00:15" or "2024-01-01 00:15:00 +01:00")
            ts = data["timestamp"].astype(str)
            data["timestamp"] = pd.to_datetime(
                ts, dayfirst=True, errors="coerce", utc=True
            ).dt.tz_localize(None)
            data["power_meter1_kw"] = pd.to_numeric(data["power_meter1_kw"], errors="coerce")
            data["power_meter2_kw"] = pd.to_numeric(data["power_meter2_kw"], errors="coerce")
            data = data.dropna(subset=["timestamp"])
            frames.append(data)

        if not frames:
            raise ValueError("No valid data rows in any Metering data sheet")
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        return combined

    def to_normalized(self, raw: pd.DataFrame) -> ConsumptionProfile:
        """Sum the two meter columns (fill NaN with 0) and build ConsumptionProfile. Consumption as positive (abs)."""
        if raw.empty:
            raise ValueError("No data to normalize")
        m1 = raw["power_meter1_kw"].fillna(0)
        m2 = raw["power_meter2_kw"].fillna(0)
        combined_kw = m1 + m2
        # Consumption as positive (Excel often has negative for consumption)
        power_kw = combined_kw.abs()
        out = pd.DataFrame({"timestamp": raw["timestamp"].values, "power_kw": power_kw.values})
        out = out.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        return ConsumptionProfile(
            data=out,
            source_identifier="adams_meetdata",
            interval_minutes=15,
        )
