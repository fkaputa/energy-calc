"""Belgian DSO (Distribution System Operator) consumption file adapter."""

import re
from pathlib import Path

import pandas as pd

from ..schema import ConsumptionProfile


class BelgianDSOAdapter:
    """Adapter for Belgian DSO electricity consumption Excel exports.

    Expected format: semicolon-separated values in first column, with headers
    Van (datum), Van (tijdstip), Register, Volume, Eenheid. Only 'Afname Actief'
    rows (active consumption in kWh) are used.
    """

    name = "belgian_dso"

    # EAN-code pattern (18 digits)
    EAN_PATTERN = re.compile(r"=\s*[\"']?(\d{18})[\"']?")
    DUTCH_HEADERS = ("Van (datum)", "Register", "Afname Actief")

    def detect(self, path: Path) -> bool:
        """Detect by content: Excel with Belgian DSO headers in first column (semicolon-separated)."""
        path = Path(path)
        if path.suffix.lower() != ".xlsx":
            return False
        try:
            df = pd.read_excel(path, header=None, engine="openpyxl", nrows=20)
            if df.empty or df.shape[1] < 1:
                return False
            first_col = df.iloc[:, 0].astype(str).str.cat(sep=" ")
            # Check for Dutch headers
            if "Van (datum)" not in first_col or "Register" not in first_col:
                return False
            if "Afname Actief" not in first_col:
                return False
            # Optional: EAN pattern
            if self.EAN_PATTERN.search(first_col):
                return True
            return True  # Header match is enough
        except Exception:
            return False

    def parse(self, path: Path) -> pd.DataFrame:
        path = Path(path)
        df = pd.read_excel(path, header=None, engine="openpyxl")
        if df.empty:
            raise ValueError("Empty file")
        # First column contains semicolon-separated data (formula result in Excel)
        col0 = df.iloc[:, 0].astype(str)
        # Parse header row
        header_row = col0.iloc[0]
        headers = [h.strip() for h in header_row.split(";")]
        if "Van (datum)" not in headers or "Register" not in headers:
            raise ValueError("Expected Belgian DSO format with Van (datum), Register columns")
        # Build parsed rows
        rows = []
        for i in range(1, len(df)):
            parts = [p.strip() for p in col0.iloc[i].split(";")]
            if len(parts) < len(headers):
                # May have merged cells / formula: B2&","&C2 style
                continue
            row_dict = dict(zip(headers, parts))
            rows.append(row_dict)
        return pd.DataFrame(rows)

    def to_normalized(self, raw: pd.DataFrame) -> ConsumptionProfile:
        if raw.empty:
            raise ValueError("No data to normalize")
        # Filter Afname Actief only
        mask = raw["Register"].astype(str).str.strip() == "Afname Actief"
        actief = raw[mask].copy()
        if actief.empty:
            raise ValueError("No 'Afname Actief' rows found")
        # Parse datetime: Van (datum) + Van (tijdstip)
        date_col = "Van (datum)"
        time_col = "Van (tijdstip)"
        if date_col not in actief.columns or time_col not in actief.columns:
            raise ValueError(f"Missing {date_col} or {time_col} columns")
        dt_str = actief[date_col].astype(str) + " " + actief[time_col].astype(str)
        actief["timestamp"] = pd.to_datetime(dt_str, format="%d-%m-%Y %H:%M:%S", errors="coerce")
        actief = actief.dropna(subset=["timestamp"])
        # Parse Volume: European comma decimal (e.g. 3,179)
        vol = actief["Volume"].astype(str).str.replace(",", ".", regex=False)
        actief["volume_kwh"] = pd.to_numeric(vol, errors="coerce")
        actief = actief.dropna(subset=["volume_kwh"])
        # Interval: 15 min = 0.25 h -> power_kw = volume_kwh / 0.25
        interval_hours = 15 / 60
        actief["power_kw"] = actief["volume_kwh"] / interval_hours
        # Extract source identifier (EAN) if present
        ean = ""
        if "EAN-code" in actief.columns:
            sample = actief["EAN-code"].iloc[0]
            if isinstance(sample, str):
                m = self.EAN_PATTERN.search(sample)
                if m:
                    ean = m.group(1)
            # Also handle raw number
            if not ean and pd.notna(sample):
                ean = str(sample).replace("=", "").replace('"', "").strip()
        out = actief[["timestamp", "power_kw"]].drop_duplicates(subset=["timestamp"]).sort_values(
            "timestamp"
        )
        return ConsumptionProfile(
            data=out,
            source_identifier=ean,
            interval_minutes=15,
        )
