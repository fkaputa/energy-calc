"""Tests for Belgian DSO adapter."""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from energy_pipeline.adapters.belgian_dso import BelgianDSOAdapter
from energy_pipeline.schema import ConsumptionProfile


def _make_belgian_dso_excel(path: Path) -> None:
    """Create a minimal Belgian DSO-style Excel for testing."""
    header = "Van (datum);Van (tijdstip);Tot (datum);Tot (tijdstip);EAN-code;Meter;Metertype;Register;Volume;Eenheid;Validatiestatus;Omschrijving"
    rows = [
        "19-12-2022;00:00:00;19-12-2022;00:15:00;=541448860000856885;;AMR-meter;Afname Actief;3,179;kWh;Gevalideerd;Bureau",
        "19-12-2022;00:00:00;19-12-2022;00:15:00;=541448860000856885;;AMR-meter;Afname Capacitief;0,151;kVArh;Gevalideerd;Bureau",
        "19-12-2022;00:15:00;19-12-2022;00:30:00;=541448860000856885;;AMR-meter;Afname Actief;3,078;kWh;Gevalideerd;Bureau",
    ]
    df = pd.DataFrame({0: [header] + rows})
    df.to_excel(path, index=False, header=False, engine="openpyxl")


def test_belgian_dso_detect() -> None:
    adapter = BelgianDSOAdapter()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        _make_belgian_dso_excel(Path(f.name))
        try:
            assert adapter.detect(Path(f.name)) is True
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_belgian_dso_detect_rejects_non_xlsx() -> None:
    adapter = BelgianDSOAdapter()
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        Path(f.name).write_text("a,b\n1,2")
        try:
            assert adapter.detect(Path(f.name)) is False
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_belgian_dso_parse_and_normalize() -> None:
    adapter = BelgianDSOAdapter()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        _make_belgian_dso_excel(Path(f.name))
        try:
            raw = adapter.parse(Path(f.name))
            assert "Register" in raw.columns
            assert "Afname Actief" in raw["Register"].values
            profile = adapter.to_normalized(raw)
            assert isinstance(profile, ConsumptionProfile)
            assert len(profile.power_kw) >= 2
            # 3.179 kWh / 0.25 h = 12.716 kW
            assert profile.power_kw.iloc[0] == pytest.approx(12.716, rel=0.01)
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_belgian_dso_power_conversion() -> None:
    adapter = BelgianDSOAdapter()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        _make_belgian_dso_excel(Path(f.name))
        try:
            raw = adapter.parse(Path(f.name))
            profile = adapter.to_normalized(raw)
            # 3.179 kWh in 15 min -> 3.179 / 0.25 = 12.716 kW
            p = profile.power_kw
            assert p.iloc[0] == pytest.approx(3.179 / 0.25, rel=0.01)
        finally:
            Path(f.name).unlink(missing_ok=True)
