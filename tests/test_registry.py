"""Tests for adapter registry."""

import tempfile
from pathlib import Path

from energy_pipeline.adapters.base import get_default_registry
from energy_pipeline.adapters.belgian_dso import BelgianDSOAdapter


def _make_belgian_dso_excel(path: Path) -> None:
    header = "Van (datum);Van (tijdstip);Tot (datum);Tot (tijdstip);EAN-code;Meter;Metertype;Register;Volume;Eenheid;Validatiestatus;Omschrijving"
    rows = [
        "19-12-2022;00:00:00;19-12-2022;00:15:00;=541448860000856885;;AMR-meter;Afname Actief;3,179;kWh;Gevalideerd;Bureau",
    ]
    import pandas as pd

    df = pd.DataFrame({0: [header] + rows})
    df.to_excel(path, index=False, header=False, engine="openpyxl")


def test_registry_detect_belgian_dso() -> None:
    registry = get_default_registry()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        _make_belgian_dso_excel(Path(f.name))
        try:
            adapter = registry.detect(Path(f.name))
            assert adapter is not None
            assert adapter.name == "belgian_dso"
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_registry_list_names() -> None:
    registry = get_default_registry()
    names = registry.list_names()
    assert "adams_meetdata" in names
    assert "belgian_dso" in names
    assert "historiek_dagtotalen" in names


def test_registry_detect_historiek_dagtotalen() -> None:
    registry = get_default_registry()
    name = "Historiek_afname_elektriciteit_541448860020554006_20250101_20260124_dagtotalen.csv"
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        p = Path(f.name)
        target = p.parent / name
        target.write_text("Datum;Afname (kWh)\n01-01-2025;24,5\n", encoding="utf-8")
        try:
            adapter = registry.detect(target)
            assert adapter is not None
            assert adapter.name == "historiek_dagtotalen"
        finally:
            target.unlink(missing_ok=True)


def test_registry_get_by_name() -> None:
    registry = get_default_registry()
    adapter = registry.get("belgian_dso")
    assert adapter is not None
    assert isinstance(adapter, BelgianDSOAdapter)


class _AcmeQuarterHourlyAdapter:
    name = "acme_quarter_hourly"

    def detect(self, path):
        return False

    def parse(self, path):
        raise NotImplementedError

    def to_normalized(self, raw):
        raise NotImplementedError


class _StubEntryPoint:
    def __init__(self, factory, name: str = "acme_quarter_hourly"):
        self._factory = factory
        self.name = name

    def load(self):
        return self._factory


def test_registry_loads_named_plugin_adapters(monkeypatch) -> None:
    from energy_pipeline.adapters import base

    monkeypatch.setattr(
        base,
        "entry_points",
        lambda group=None: [_StubEntryPoint(lambda: _AcmeQuarterHourlyAdapter())]
        if group == "energy_pipeline.adapters"
        else [],
    )

    registry = get_default_registry()
    assert "acme_quarter_hourly" in registry.list_names()


def test_registry_ignores_broken_plugin_adapter(monkeypatch) -> None:
    from energy_pipeline.adapters import base

    def _broken_factory():
        raise RuntimeError("boom")

    monkeypatch.setattr(
        base,
        "entry_points",
        lambda group=None: [_StubEntryPoint(_broken_factory)]
        if group == "energy_pipeline.adapters"
        else [],
    )

    registry = get_default_registry()
    assert "acme_quarter_hourly" not in registry.list_names()


class _NamelessAdapter:
    def detect(self, path):
        return False

    def parse(self, path):
        raise NotImplementedError

    def to_normalized(self, raw):
        raise NotImplementedError


def test_registry_sets_fallback_name_for_nameless_plugin(monkeypatch) -> None:
    from energy_pipeline.adapters import base

    monkeypatch.setattr(
        base,
        "entry_points",
        lambda group=None: [_StubEntryPoint(lambda: _NamelessAdapter(), "GridFlex Adapter v2")]
        if group == "energy_pipeline.adapters"
        else [],
    )

    registry = get_default_registry()
    assert "plugin_gridflex_adapter_v2" in registry.list_names()
