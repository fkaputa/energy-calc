"""Tests for Historiek afname elektriciteit dagtotalen CSV adapter."""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from energy_pipeline.adapters.historiek_dagtotalen import (
    HistoriekDagtotalenAdapter,
    extract_ean_from_path,
)
from energy_pipeline.schema import ConsumptionProfile


def _make_historiek_csv(path: Path, sep: str = ";", date_col: str = "Datum", cons_col: str = "Afname (kWh)") -> None:
    """Create a minimal Historiek dagtotalen-style CSV for testing."""
    content = f"{date_col}{sep}{cons_col}\n"
    content += f"01-01-2025{sep}24,5\n"
    content += f"02-01-2025{sep}30,0\n"
    content += f"03-01-2025{sep}18,25\n"
    path.write_text(content, encoding="utf-8")


def test_historiek_detect_by_content() -> None:
    """Detection is by content (headers), not filename."""
    adapter = HistoriekDagtotalenAdapter()
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        Path(f.name).write_text("Datum;Afname (kWh)\n01-01-2025;10\n02-01-2025;12", encoding="utf-8")
        try:
            assert adapter.detect(Path(f.name)) is True
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_historiek_detect_by_content_with_historiek_filename() -> None:
    """Detection works with the typical Historiek filename too."""
    adapter = HistoriekDagtotalenAdapter()
    name = "Historiek_afname_elektriciteit_541448860020554006_20250101_20260124_dagtotalen.csv"
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        p = Path(f.name)
        target = p.parent / name
        _make_historiek_csv(target)
        try:
            assert adapter.detect(target) is True
        finally:
            target.unlink(missing_ok=True)


def test_historiek_detect_rejects_wrong_columns() -> None:
    """Reject when content lacks date + consumption headers."""
    adapter = HistoriekDagtotalenAdapter()
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        Path(f.name).write_text("Foo;Bar\n1;2", encoding="utf-8")
        try:
            assert adapter.detect(Path(f.name)) is False
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_historiek_parse_and_normalize() -> None:
    adapter = HistoriekDagtotalenAdapter()
    name = "Historiek_afname_elektriciteit_541448860020554006_20250101_20260124_dagtotalen.csv"
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        p = Path(f.name)
        target = p.parent / name
        _make_historiek_csv(target)
        try:
            raw = adapter.parse(target)
            assert "Datum" in raw.columns or any("atum" in c for c in raw.columns)
            assert len(raw) == 3
            if hasattr(raw, "attrs"):
                raw.attrs["path"] = target
            profile = adapter.to_normalized(raw)
            assert isinstance(profile, ConsumptionProfile)
            # 3 days * 96 intervals/day = 288 rows
            assert len(profile.power_kw) == 3 * 96
            # First day: 24.5 kWh / 24 h = 1.0208... kW
            assert profile.power_kw.iloc[0] == pytest.approx(24.5 / 24.0, rel=0.01)
            assert profile.interval_minutes == 15
        finally:
            target.unlink(missing_ok=True)


def test_historiek_ean_from_path() -> None:
    name = "Historiek_afname_elektriciteit_541448860020554006_20250101_20260124_dagtotalen.csv"
    assert extract_ean_from_path(Path(name)) == "541448860020554006"
    assert extract_ean_from_path(Path("other.csv")) == ""


def test_historiek_source_identifier_when_path_in_attrs() -> None:
    adapter = HistoriekDagtotalenAdapter()
    name = "Historiek_afname_elektriciteit_541448860020554006_20250101_20260124_dagtotalen.csv"
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        p = Path(f.name)
        target = p.parent / name
        _make_historiek_csv(target)
        try:
            raw = adapter.parse(target)
            raw.attrs["path"] = target
            profile = adapter.to_normalized(raw)
            assert profile.source_identifier == "541448860020554006"
        finally:
            target.unlink(missing_ok=True)


def test_historiek_comma_separator() -> None:
    """Comma-separated CSV: use dot decimal to avoid column split (e.g. 24.5 not 24,5)."""
    adapter = HistoriekDagtotalenAdapter()
    name = "Historiek_afname_elektriciteit_541448860020554006_20250101_20260124_dagtotalen.csv"
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        p = Path(f.name)
        target = p.parent / name
        # Use dot decimal so comma sep doesn't split one value into two columns
        content = "Datum,Afname (kWh)\n01-01-2025,24.5\n02-01-2025,30.0\n03-01-2025,18.25\n"
        target.write_text(content, encoding="utf-8")
        try:
            raw = adapter.parse(target)
            assert len(raw) == 3
            raw.attrs["path"] = target
            profile = adapter.to_normalized(raw)
            assert len(profile.power_kw) == 3 * 96
        finally:
            target.unlink(missing_ok=True)
