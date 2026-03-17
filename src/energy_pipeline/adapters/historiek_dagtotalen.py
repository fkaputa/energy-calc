"""Adapter for Historiek afname elektriciteit dagtotalen CSV exports.

File pattern: Historiek_afname_elektriciteit_<EAN>_<YYYYMMDD>_<YYYYMMDD>_dagtotalen.csv
Contains daily electricity consumption totals (kWh per day). Converted to 15-min
average power (kW) for pipeline compatibility.
"""

import re
from pathlib import Path

import pandas as pd

from ..schema import ConsumptionProfile


# Filename: Historiek_afname_elektriciteit_541448860020554006_20250101_20260124_dagtotalen.csv
FILENAME_PATTERN = re.compile(
    r"Historiek_afname_elektriciteit_(\d+)_\d{8}_\d{8}_dagtotalen\.csv",
    re.IGNORECASE,
)

# Possible column names for date and consumption (Dutch/Belgian exports)
# Exact and substring match; add variants seen in real exports
DATE_COLUMN_CANDIDATES = (
    "Datum",
    "datum",
    "Date",
    "date",
    "Van",
    "van",
    "Datum (dd/mm/yyyy)",
    "Dag",
    "Datum/tijd",
)
CONSUMPTION_COLUMN_CANDIDATES = (
    "Afname (kWh)",
    "Afname",
    "afname",
    "Verbruik (kWh)",
    "Verbruik",
    "verbruik",
    "Volume",
    "volume",
    "kWh",
    "Energie (kWh)",
    "Energie",
    "Dagtotalen",
    "Dagtotalen (kWh)",
    "Afname in kWh",
    "Verbruik in kWh",
)


class HistoriekDagtotalenAdapter:
    """Adapter for Belgian/Dutch 'Historiek afname elektriciteit dagtotalen' CSV.

    Expects CSV with a date column and a consumption column (daily totals in kWh).
    Supports semicolon or comma separator and European number format (comma decimal).
    """

    name = "historiek_dagtotalen"

    def detect(self, path: Path) -> bool:
        """Detect by content: CSV with date and consumption headers (no filename check)."""
        path = Path(path)
        if path.suffix.lower() != ".csv":
            return False
        encodings = ("utf-8-sig", "utf-8", "latin-1", "cp1252")
        try:
            for sep in (";", ","):
                for encoding in encodings:
                    try:
                        df = pd.read_csv(path, sep=sep, nrows=10, encoding=encoding)
                    except Exception:
                        continue
                    if df.empty or df.shape[1] < 2:
                        continue
                    cols = [str(c).strip() for c in df.columns]
                    date_col = self._find_column(cols, DATE_COLUMN_CANDIDATES)
                    cons_col = self._find_column(cols, CONSUMPTION_COLUMN_CANDIDATES)
                    if (
                        date_col is not None
                        and cons_col is not None
                        and date_col != cons_col
                    ):
                        return True
        except Exception:
            pass
        return False

    def _find_column(self, columns: list, candidates: tuple) -> str | None:
        for c in candidates:
            if c in columns:
                return c
        # Case-insensitive exact match
        lower_cols = {str(x).strip().lower(): x for x in columns}
        for cand in candidates:
            if cand.lower() in lower_cols:
                return lower_cols[cand.lower()]
        # Substring match: any column name containing a keyword (e.g. "Datum (dd/mm/yyyy)")
        for col in columns:
            col_lower = col.lower()
            for cand in candidates:
                if cand.lower() in col_lower:
                    return col
        return None

    def parse(self, path: Path) -> pd.DataFrame:
        path = Path(path)
        for sep in (";", ","):
            try:
                df = pd.read_csv(path, sep=sep, encoding="utf-8")
            except Exception:
                df = pd.read_csv(path, sep=sep, encoding="latin-1")
            if df.empty or df.shape[1] < 2:
                continue
            df.columns = [str(c).strip() for c in df.columns]
            date_col = self._find_column(list(df.columns), DATE_COLUMN_CANDIDATES)
            cons_col = self._find_column(list(df.columns), CONSUMPTION_COLUMN_CANDIDATES)
            if date_col is not None and cons_col is not None:
                return df[[date_col, cons_col]].copy()
        raise ValueError(
            "Could not parse Historiek dagtotalen CSV: no date and consumption columns found. "
            "Expected columns like 'Datum' and 'Afname (kWh)' or similar."
        )

    def to_normalized(self, raw: pd.DataFrame) -> ConsumptionProfile:
        if raw.empty:
            raise ValueError("No data to normalize")
        cols = list(raw.columns)
        date_col = self._find_column(cols, DATE_COLUMN_CANDIDATES)
        cons_col = self._find_column(cols, CONSUMPTION_COLUMN_CANDIDATES)
        if date_col is None or cons_col is None:
            raise ValueError("Missing date or consumption column in raw data")

        df = raw[[date_col, cons_col]].copy()
        # Parse date (dd/mm/yyyy, yyyy-mm-dd, etc.)
        df["_date"] = pd.to_datetime(df[date_col], dayfirst=True, errors="coerce")
        # Parse consumption: European comma decimal
        vol = df[cons_col].astype(str).str.replace(",", ".", regex=False)
        df["_kwh"] = pd.to_numeric(vol, errors="coerce")
        df = df.dropna(subset=["_date", "_kwh"])

        if len(df) == 0:
            raise ValueError("No valid date/consumption rows in Historiek dagtotalen file")

        dates_arr = df["_date"].values
        kwh_arr = df["_kwh"].values

        # Daily total kWh -> average power over the day (kW)
        # Then expand to 15-min intervals so pipeline gets consistent resolution
        interval_minutes = 15
        minutes_per_day = 24 * 60
        intervals_per_day = minutes_per_day // interval_minutes  # 96

        rows = []
        for j in range(len(dates_arr)):
            dt = dates_arr[j]
            daily_kwh = kwh_arr[j]
            avg_kw = float(daily_kwh) / 24.0
            start = pd.Timestamp(dt).normalize()
            for i in range(intervals_per_day):
                ts = start + pd.Timedelta(minutes=interval_minutes * i)
                rows.append({"timestamp": ts, "power_kw": avg_kw})

        out = pd.DataFrame(rows)
        out = out.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")

        # EAN from filename (pipeline may set raw.attrs["path"])
        source_identifier = ""
        path_val = getattr(raw, "attrs", None) and raw.attrs.get("path")
        if path_val:
            source_identifier = extract_ean_from_path(Path(path_val))

        return ConsumptionProfile(
            data=out,
            source_identifier=source_identifier,
            interval_minutes=interval_minutes,
        )


def extract_ean_from_path(path: Path) -> str:
    """Extract EAN (meter ID) from Historiek dagtotalen filename."""
    m = FILENAME_PATTERN.search(path.name)
    return m.group(1) if m else ""
