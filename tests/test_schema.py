"""Tests for ConsumptionProfile and NormalizedConfig."""

import pandas as pd
import pytest

from energy_pipeline.schema import ConsumptionProfile, NormalizedConfig


def test_consumption_profile_from_dataframe() -> None:
    ts = pd.date_range("2022-01-01", periods=4, freq="15min")
    df = pd.DataFrame({"timestamp": ts, "power_kw": [10.0, 20.0, 15.0, 25.0]})
    profile = ConsumptionProfile(data=df)
    assert len(profile.timestamps) == 4
    assert profile.power_kw.iloc[0] == 10.0
    assert profile.power_mw.iloc[0] == 0.01


def test_consumption_profile_from_series() -> None:
    ts = pd.date_range("2022-01-01", periods=4, freq="15min")
    s = pd.Series([10.0, 20.0, 15.0, 25.0], index=ts)
    profile = ConsumptionProfile.from_series(s, source_identifier="EAN123")
    assert profile.source_identifier == "EAN123"
    assert list(profile.power_kw.values) == [10.0, 20.0, 15.0, 25.0]


def test_consumption_profile_invalid_data() -> None:
    df = pd.DataFrame({"a": [1], "b": [2]})
    with pytest.raises(ValueError, match="power_kw"):
        ConsumptionProfile(data=df)


def test_normalized_config_defaults() -> None:
    config = NormalizedConfig()
    assert config.peak_start_hour == 7
    assert config.peak_end_hour == 21
    assert config.battery_max_hours == 4.0
