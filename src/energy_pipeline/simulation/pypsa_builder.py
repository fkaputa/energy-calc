"""Build PyPSA network and run battery optimization."""

from typing import Optional, Tuple, Union

import numpy as np
import pandas as pd
import pypsa
from pypsa.common import annuity

from ..schema import ConsumptionProfile, NormalizedConfig


def build_time_of_use_prices(
    snapshots: pd.DatetimeIndex,
    config: NormalizedConfig,
) -> pd.Series:
    """Build marginal cost series (€/MWh) from time-of-use config."""
    hours = snapshots.hour
    peak_mask = (hours >= config.peak_start_hour) & (hours < config.peak_end_hour)
    prices = np.where(peak_mask, config.peak_price_eur_mwh, config.off_peak_price_eur_mwh)
    return pd.Series(prices, index=snapshots)


def build_and_optimize(
    profile: ConsumptionProfile,
    config: Optional[NormalizedConfig] = None,
    log_to_console: bool = False,
    pv_profile: Optional[ConsumptionProfile] = None,
) -> Tuple[pypsa.Network, Union[str, Tuple[str, str]]]:
    """Build PyPSA network and run optimization.

    Args:
        profile: Normalized consumption profile (power in kW)
        config: Simulation config; uses defaults if None
        log_to_console: Passed to n.optimize()
        pv_profile: Optional PV generation profile (power in kW); if given, added as generator.

    Returns:
        (network, status) e.g. ('ok', 'optimal')
    """
    if config is None:
        config = NormalizedConfig()
    load_mw = profile.to_series()
    snapshots = load_mw.index.drop_duplicates().sort_values()
    peak_load_mw = float(load_mw.reindex(snapshots).fillna(0).max())
    if peak_load_mw > config.grid_p_nom_mw:
        raise ValueError(
            f"Peak load {peak_load_mw:.2f} MW exceeds grid_p_nom_mw {config.grid_p_nom_mw}. "
            "Increase grid_p_nom_mw in config (or reduce load) so the problem is feasible."
        )
    n = pypsa.Network()
    n.set_snapshots(snapshots)
    # Ensure valid snapshot weightings (NaN/zero can make storage constraints infeasible)
    sw = n.snapshot_weightings
    if sw.isna().any().any() or (sw <= 0).any().any():
        n.snapshot_weightings = pd.DataFrame(
            {"objective": 1.0, "stores": 1.0, "generators": 1.0}, index=snapshots
        )
    n.add("Carrier", ["AC", "grid", "battery", "solar"])
    n.add("Bus", "site", carrier="AC")
    n.add("Load", "demand", bus="site", p_set=load_mw.reindex(snapshots).fillna(0))
    # PV generator: upper bound = profile (allows curtailment when PV > load + export + charge capacity)
    if pv_profile is not None:
        pv_series = pv_profile.to_series().sort_index()
        # Align to load snapshots; forward-fill so hourly PV is spread over 15-min slots
        pv_mw = pv_series.reindex(snapshots).ffill().fillna(0)
        pv_max = float(pv_mw.max())
        if pv_max > 0:
            # p_nom = max PV so p_max_pu in [0,1]; optimizer can curtail when export/charge is saturated
            n.add(
                "Generator",
                "pv",
                bus="site",
                p_nom=pv_max,
                p_max_pu=pv_mw / pv_max,
                marginal_cost=0,
                carrier="solar",
            )
    # Grid generator with ToU pricing; allow export (p_min_pu=-1) when PV exceeds load
    price_series = build_time_of_use_prices(snapshots, config)
    n.add(
        "Generator",
        "grid",
        bus="site",
        p_nom=config.grid_p_nom_mw,
        p_min_pu=-1.0,  # allow export so PV excess can be fed to grid (avoids infeasibility)
        marginal_cost=price_series,
        carrier="grid",
    )
    # Battery storage
    eta = np.sqrt(config.battery_round_trip_efficiency)
    cc_inv = annuity(config.discount_rate, config.battery_lifetime_years) * config.battery_inverter_cost_eur_kw * 1000
    cc_storage = annuity(config.discount_rate, config.battery_lifetime_years) * config.battery_capital_cost_eur_kwh * 1000
    capital_cost = cc_inv + config.battery_max_hours * cc_storage
    storage_attrs = dict(
        bus="site",
        p_nom_extendable=True,
        carrier="battery",
        efficiency_store=eta,
        efficiency_dispatch=eta,
        max_hours=config.battery_max_hours,
        capital_cost=capital_cost,
        cyclic_state_of_charge=False,  # avoid infeasibility from end-state constraint
    )
    if config.battery_p_nom_max_mw is not None:
        storage_attrs["p_nom_max"] = config.battery_p_nom_max_mw
    n.add(
        "StorageUnit",
        "battery",
        **storage_attrs,
    )
    status = n.optimize(log_to_console=log_to_console)
    return n, status
