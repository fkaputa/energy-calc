"""Battery / time-of-use configuration and EA-Sim configuration loader."""

from pathlib import Path
from typing import Any, Dict

import yaml

from ..schema import NormalizedConfig
from .distribution_costs import DistCostParams
from .ea_sim import EaSimConfig


def load_raw_config(path: Path) -> Dict[str, Any]:
    """Return the raw YAML dictionary (empty dict when file is missing)."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_config(path: Path) -> NormalizedConfig:
    """Load configuration from YAML file."""
    return config_from_dict(load_raw_config(path))


def config_from_dict(data: Dict[str, Any]) -> NormalizedConfig:
    """Build NormalizedConfig from dictionary (e.g. from YAML)."""
    battery = data.get("battery", {})
    return NormalizedConfig(
        resample_rule=data.get("resample_rule", "15min"),
        peak_start_hour=data.get("peak_start_hour", 7),
        peak_end_hour=data.get("peak_end_hour", 21),
        peak_price_eur_mwh=data.get("peak_price_eur_mwh", 150.0),
        off_peak_price_eur_mwh=data.get("off_peak_price_eur_mwh", 80.0),
        battery_round_trip_efficiency=battery.get("round_trip_efficiency", 0.9),
        battery_max_hours=battery.get("max_hours", 4.0),
        battery_p_nom_max_mw=battery.get("p_nom_max_mw"),
        battery_capital_cost_eur_kwh=battery.get("capital_cost_eur_kwh", 150.0),
        battery_inverter_cost_eur_kw=battery.get("inverter_cost_eur_kw", 170.0),
        battery_lifetime_years=battery.get("lifetime_years", 25.0),
        discount_rate=battery.get("discount_rate", 0.05),
        grid_p_nom_mw=data.get("grid_p_nom_mw", 1000.0),
    )


def _load_dist_cost_params(dist: Dict[str, Any]) -> DistCostParams:
    """Build DistCostParams from the ``distribution_costs:`` YAML block."""
    return DistCostParams(
        is_stationary=dist.get("is_stationary", False),
        elia_fraction=dist.get("elia_fraction", 0.0),
        connection_price_eur_per_mw=dist.get("connection_price_eur_per_mw", 40_684.4988),
        peak_price_eur_per_mw=dist.get("peak_price_eur_per_mw", 59_856.96),
        odv_eur_per_mwh=dist.get("odv_eur_per_mwh", 3.9196),
        surcharge_eur_per_mwh=dist.get("surcharge_eur_per_mwh", 0.3058),
        offtake_base_eur_per_mwh=dist.get("offtake_base_eur_per_mwh", 29.14),
        injection_eur_per_mwh=dist.get("injection_eur_per_mwh", 1.751),
        fixed_daily_eur=dist.get("fixed_daily_eur", 7.2896),
        capacity_cap_eur_per_mwh=dist.get("capacity_cap_eur_per_mwh", 150.8082),
        stationary_discount=dist.get("stationary_discount", 0.8),
    )


def load_ea_sim_config(path: Path) -> EaSimConfig:
    """Load EaSimConfig from the ``ea_sim:`` section of a YAML config file."""
    raw = load_raw_config(path)
    ea = raw.get("ea_sim", {})
    dist = ea.get("distribution_costs", {})
    return EaSimConfig(
        battery_capacity_kwh=ea.get("battery_capacity_kwh", 1000.0),
        battery_power_kw=ea.get("battery_power_kw"),
        round_trip_efficiency=ea.get("round_trip_efficiency", 0.88),
        connection_capacity_kw=ea.get("connection_capacity_kw", 1700.0),
        injection_limit_kw=ea.get("injection_limit_kw", 1300.0),
        high_power_threshold_kw=ea.get("high_power_threshold_kw"),
        strategy=ea.get("strategy", "peak_shaving"),
        off_peak_battery=ea.get("off_peak_battery", False),
        off_peak_start_hour=int(ea.get("off_peak_start_hour", 21)),
        off_peak_end_hour=int(ea.get("off_peak_end_hour", 7)),
        dist_cost_enabled=dist.get("enabled", True),
        dist_cost_calculator=dist.get("calculator", "belgian_general"),
        dist_cost_params=_load_dist_cost_params(dist),
    )


# Alias for clarity
BatteryConfig = NormalizedConfig
