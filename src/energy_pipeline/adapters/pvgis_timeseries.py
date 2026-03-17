"""Adapter for PVGIS timeseries CSV exports (time, P columns; P in W)."""

from pathlib import Path

import pandas as pd

from ..schema import ConsumptionProfile


class PvgisTimeseriesAdapter:
    """Adapter for PVGIS timeseries CSV files.

    Expects CSV with optional metadata header lines, then a row "time,P,...".
    - time: format YYYYMMDD:HHMM (e.g. 20200101:0910)
    - P: power in W (converted to kW for normalized profile)
    """

    name = "pvgis_timeseries"

    def detect(self, path: Path) -> bool:
        """Detect by file extension and content: CSV with 'time,P,' header."""
        path = Path(path)
        if path.suffix.lower() != ".csv":
            return False
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= 20:
                        break
                    line = line.strip()
                    if line.startswith("time,") and ",P," in line:
                        return True
        except Exception:
            pass
        return False

    def parse(self, path: Path) -> pd.DataFrame:
        """Parse PVGIS CSV: skip metadata lines, then read time and P columns."""
        path = Path(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        header_row_idx = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("time,") or (stripped.startswith("time") and ",P," in stripped):
                header_row_idx = i
                break
        if header_row_idx is None:
            raise ValueError("PVGIS CSV must contain a header row 'time,P,...'")

        df = pd.read_csv(
            path,
            skiprows=header_row_idx,
            usecols=["time", "P"],
        )
        # time format: 20200101:0910 -> 2020-01-01 09:10:00
        def parse_time(s: str):
            s = str(s).strip()
            if len(s) >= 12 and ":" in s:
                date_part, time_part = s.split(":", 1)
                if len(date_part) == 8 and len(time_part) >= 4:  # YYYYMMDD, HHMM
                    return f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]} {time_part[:2]}:{time_part[2:4]}:00"
            return None

        df["timestamp"] = df["time"].astype(str).apply(parse_time)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df["power_kw"] = pd.to_numeric(df["P"], errors="coerce") / 1000.0  # W -> kW
        df = df.dropna(subset=["timestamp", "power_kw"])
        out = df[["timestamp", "power_kw"]].drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        return out

    def to_normalized(self, raw: pd.DataFrame) -> ConsumptionProfile:
        """Build ConsumptionProfile from parsed time and power_kw (same schema as consumption)."""
        if raw.empty:
            raise ValueError("No data to normalize")
        out = raw[["timestamp", "power_kw"]].copy()
        out = out.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        # PVGIS hourly data -> 60 min interval; 10-min if present would be 10
        interval_minutes = 60
        if len(out) >= 2:
            delta = (out["timestamp"].iloc[1] - out["timestamp"].iloc[0]).total_seconds() / 60
            if 0 < delta <= 60:
                interval_minutes = int(delta)
        return ConsumptionProfile(
            data=out,
            source_identifier="pvgis",
            interval_minutes=interval_minutes,
        )
