"""EnergyLabs rule-based battery (BESS) simulation.

A lightweight alternative to PyPSA optimization that uses deterministic rules
translated from the QlikView simulation logic.  Two strategies are available:

- **pv_self_consumption** — charge from PV surplus, discharge into all demand.
- **peak_shaving** — look-ahead strategy that detects upcoming high-demand
  regimes, pre-charges the battery, and discharges to keep grid power below
  a configurable threshold. Optional ``off_peak_battery``: during off-peak
  hours charge from grid to build a buffer for the next day based on the
  previous day's peaks.

Results are exported in PyPSA-compatible CSV format so the existing
visualization pipeline works without changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, cast

import numpy as np
import pandas as pd

from ..schema import ConsumptionProfile
from .distribution_costs import DistCostParams, available_calculators, get_calculator


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class EaSimConfig:
    """Configuration for EnergyLabs BESS simulation."""

    battery_capacity_kwh: float = 1000.0
    battery_power_kw: Optional[float] = None
    round_trip_efficiency: float = 0.88
    connection_capacity_kw: float = 1700.0
    injection_limit_kw: float = 1300.0
    high_power_threshold_kw: Optional[float] = None
    strategy: str = "peak_shaving"

    # Off-peak pre-charge for peak shaving: build buffer during off-peak using previous day's peaks
    off_peak_battery: bool = False
    off_peak_start_hour: int = 21  # hour when off-peak starts (e.g. 21 = 21:00)
    off_peak_end_hour: int = 7    # hour when off-peak ends (e.g. 7 = 07:00); off-peak = hour >= start or hour < end

    # Distribution cost configuration
    dist_cost_enabled: bool = True
    dist_cost_calculator: str = "belgian_general"
    dist_cost_params: DistCostParams = field(default_factory=DistCostParams)

    def __post_init__(self) -> None:
        if self.battery_power_kw is None:
            self.battery_power_kw = self.battery_capacity_kwh / 2.0
        if self.high_power_threshold_kw is None:
            self.high_power_threshold_kw = self.connection_capacity_kw * 0.7


# ---------------------------------------------------------------------------
# BESS strategies
# ---------------------------------------------------------------------------

def _simulate_bess_pv_self_consumption(
    net_power_kw: np.ndarray,
    battery_kwh: float,
    battery_kw: float,
    steps_per_hour: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Charge from PV surplus (power < 0), discharge into all demand (power > 0).

    Matches the QlikView PV_BESS tab logic (ELSE branch, which is always active).
    """
    n = len(net_power_kw)
    bess_power = np.zeros(n)
    bess_soc = np.zeros(n)

    for i in range(n):
        prev_soc = bess_soc[i - 1] if i > 0 else 0.0
        p = net_power_kw[i]

        if p < 0:
            available_space = max(battery_kwh - max(0.0, prev_soc), 0.0)
            bess_power[i] = min(available_space * steps_per_hour, battery_kw, -p)
        else:
            available_energy = min(max(0.0, prev_soc), battery_kwh)
            bess_power[i] = -min(available_energy * steps_per_hour, battery_kw, p)

        bess_soc[i] = np.clip(
            prev_soc + bess_power[i] / steps_per_hour, 0.0, battery_kwh
        )

    return bess_power, bess_soc


def _detect_high_power_regimes(
    power_kw: np.ndarray,
    threshold_kw: float,
    steps_per_hour: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Identify contiguous high / low demand sequences and their energy.

    Returns
    -------
    regime_ids : int array — regime id per timestep (increments on crossing)
    regime_flags : int array — 1 where power > threshold
    next_regime_kwh : float array — energy (kWh) of the *next* regime for
        each timestep (used for look-ahead pre-charging)
    """
    n = len(power_kw)
    regime_flags = (power_kw > threshold_kw).astype(np.int32)

    regime_ids = np.zeros(n, dtype=np.int32)
    for i in range(1, n):
        if regime_flags[i] != regime_flags[i - 1]:
            regime_ids[i] = regime_ids[i - 1] + 1
        else:
            regime_ids[i] = regime_ids[i - 1]

    max_id = int(regime_ids[-1]) if n else 0
    regime_energy = np.zeros(max_id + 1)
    for rid in range(max_id + 1):
        mask = regime_ids == rid
        regime_energy[rid] = np.sum(power_kw[mask] - threshold_kw) / steps_per_hour

    next_energy = np.zeros(max_id + 1)
    for rid in range(max_id):
        next_energy[rid] = regime_energy[rid + 1]

    next_regime_kwh = next_energy[regime_ids]
    return regime_ids, regime_flags, next_regime_kwh


def _daily_energy_above_threshold(
    net_power_kw: np.ndarray,
    threshold_kw: float,
    steps_per_hour: float,
    snapshots: pd.DatetimeIndex,
) -> dict[pd.Timestamp, float]:
    """Per calendar day: total energy (kWh) above threshold (for off-peak target buffer)."""
    from datetime import date

    n = len(net_power_kw)
    step_energy_kwh = 1.0 / steps_per_hour  # energy per step in kWh at 1 kW
    daily: dict[date, float] = {}
    for i in range(n):
        if i < len(snapshots):
            ts = cast("pd.Timestamp", snapshots[i])
            d_key: date = ts.date()
        else:
            d_key = date(2000, 1, 1)
        excess_kw = max(0.0, net_power_kw[i] - threshold_kw)
        prev = daily.get(d_key, 0.0)
        daily[d_key] = prev + excess_kw * step_energy_kwh
    # Cast keys to pandas.Timestamp for downstream use
    # Explicitly construct a dict with pandas.Timestamp keys to satisfy type checkers
    result_dict: dict[pd.Timestamp, float] = {}
    for d_key, value in daily.items():
        ts_key: pd.Timestamp = pd.Timestamp(d_key)  # type: ignore[reportAssignmentType]
        result_dict[ts_key] = value
    return result_dict


def _is_off_peak(hour: int, start_hour: int, end_hour: int) -> bool:
    """True if hour (0-23) falls in off-peak window (e.g. hour >= 21 or hour < 7)."""
    if start_hour > end_hour:  # overnight window
        return hour >= start_hour or hour < end_hour
    return end_hour <= hour < start_hour


def _simulate_bess_peak_shaving(
    net_power_kw: np.ndarray,
    battery_kwh: float,
    battery_kw: float,
    threshold_kw: float,
    steps_per_hour: float,
    snapshots: Optional[pd.DatetimeIndex] = None,
    off_peak_battery: bool = False,
    off_peak_start_hour: int = 21,
    off_peak_end_hour: int = 7,
) -> tuple[np.ndarray, np.ndarray]:
    """Peak-shaving BESS with regime look-ahead and optional off-peak pre-charge.

    - PV surplus → charge.
    - If off_peak_battery and in off-peak and SoC < target (from previous day's peaks):
      → charge from grid to build buffer for next day.
    - Not in high regime, upcoming high regime needs more energy than SoC
      → charge from grid (limited by headroom below threshold).
    - Not in high regime, SoC exceeds upcoming need → partially discharge.
    - In high regime → discharge to cap power at *threshold_kw*.
    - Otherwise → discharge into all demand.
    """
    _, regime_flags, next_regime_kwh = _detect_high_power_regimes(
        net_power_kw, threshold_kw, steps_per_hour
    )
    n = len(net_power_kw)
    bess_power = np.zeros(n)
    bess_soc = np.zeros(n)

    # Target buffer (kWh) per timestep when off_peak_battery: use same day's energy above threshold
    # as anticipated need for *next* day, so we charge during off-peak to reach that by morning.
    target_buffer_kwh: Optional[np.ndarray] = None
    if off_peak_battery and snapshots is not None and len(snapshots) == n:
        daily_kwh = _daily_energy_above_threshold(
            net_power_kw, threshold_kw, steps_per_hour, snapshots
        )
        # For timestep i on date D: target for end of off-peak = need for day D+1 = energy above threshold on day D
        target_buffer_kwh = np.zeros(n)
        for i in range(n):
            ts = cast("pd.Timestamp", snapshots[i])
            ts_d = pd.Timestamp(ts.date())
            prev = daily_kwh.get(ts_d, 0.0)  # type: ignore[reportArgumentType]
            target_buffer_kwh[i] = min(prev, battery_kwh)

    for i in range(n):
        prev_soc = bess_soc[i - 1] if i > 0 else 0.0
        p = net_power_kw[i]
        flag = regime_flags[i]
        next_kwh = next_regime_kwh[i]

        if p < 0:
            # PV surplus → charge
            available_space = max(battery_kwh - max(0.0, prev_soc), 0.0)
            bess_power[i] = min(available_space * steps_per_hour, battery_kw, -p)

        elif (
            off_peak_battery
            and target_buffer_kwh is not None
            and prev_soc < target_buffer_kwh[i]
        ):
            hour = (
                int(cast("pd.Timestamp", snapshots[i]).hour)
                if snapshots is not None and i < len(snapshots)
                else 12
            )
            if _is_off_peak(hour, off_peak_start_hour, off_peak_end_hour):
                # Off-peak: build buffer for next day (target from same day's consumption)
                available_space = max(battery_kwh - max(0.0, prev_soc), 0.0)
                headroom = max(0.0, threshold_kw - p)
                need_kwh = target_buffer_kwh[i] - prev_soc
                charge_power = min(
                    available_space * steps_per_hour,
                    battery_kw,
                    headroom,
                    need_kwh * steps_per_hour,
                )
                bess_power[i] = max(0.0, charge_power)
            else:
                # Not off-peak: fall through to existing regime logic below (no goto, so duplicate branch)
                if flag == 0 and next_kwh > 0:
                    if next_kwh > prev_soc:
                        available_space = max(battery_kwh - max(0.0, prev_soc), 0.0)
                        headroom = max(0.0, threshold_kw - p)
                        bess_power[i] = min(
                            available_space * steps_per_hour, battery_kw, headroom
                        )
                    else:
                        excess = max(0.0, prev_soc - next_kwh)
                        bess_power[i] = -min(
                            excess * steps_per_hour, battery_kw, p
                        )
                elif flag == 1:
                    available_energy = min(max(0.0, prev_soc), battery_kwh)
                    overshoot = max(0.0, p - threshold_kw)
                    bess_power[i] = -min(
                        available_energy * steps_per_hour, battery_kw, overshoot
                    )
                else:
                    available_energy = min(max(0.0, prev_soc), battery_kwh)
                    bess_power[i] = -min(
                        available_energy * steps_per_hour, battery_kw, p
                    )

        elif flag == 0 and next_kwh > 0:
            # Not in high regime; an upcoming high regime needs energy
            if next_kwh > prev_soc:
                # Need to pre-charge — limited by headroom below threshold
                available_space = max(battery_kwh - max(0.0, prev_soc), 0.0)
                headroom = max(0.0, threshold_kw - p)
                bess_power[i] = min(
                    available_space * steps_per_hour, battery_kw, headroom
                )
            else:
                # Enough stored; discharge excess above what next regime needs
                excess = max(0.0, prev_soc - next_kwh)
                bess_power[i] = -min(
                    excess * steps_per_hour, battery_kw, p
                )

        elif flag == 1:
            # In high regime → discharge to reduce overshoot above threshold
            available_energy = min(max(0.0, prev_soc), battery_kwh)
            overshoot = max(0.0, p - threshold_kw)
            bess_power[i] = -min(
                available_energy * steps_per_hour, battery_kw, overshoot
            )

        else:
            # No upcoming high regime → discharge into all demand
            available_energy = min(max(0.0, prev_soc), battery_kwh)
            bess_power[i] = -min(
                available_energy * steps_per_hour, battery_kw, p
            )

        bess_soc[i] = np.clip(
            prev_soc + bess_power[i] / steps_per_hour, 0.0, battery_kwh
        )

    return bess_power, bess_soc


# ---------------------------------------------------------------------------
# Belgian distribution-cost calculation (delegated to distribution_costs module)
# ---------------------------------------------------------------------------

def _compute_monthly_distribution_costs(
    power_kw: pd.Series,
    snapshots: pd.DatetimeIndex,
    connection_kw: float,
    steps_per_hour: float,
    config: EaSimConfig,
    calculator_name: Optional[str] = None,
) -> pd.DataFrame:
    """Monthly Belgian distribution costs via the specified (or configured) calculator."""
    name = calculator_name or config.dist_cost_calculator
    calculator = get_calculator(name, config.dist_cost_params)
    return calculator.compute_monthly_from_timeseries(
        power_kw, snapshots, connection_kw, steps_per_hour,
    )


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class EaSimResult:
    """Results from EA BESS simulation."""

    snapshots: pd.DatetimeIndex
    interval_minutes: int
    consumption_kw: pd.Series
    pv_kw: pd.Series
    net_power_kw: pd.Series
    bess_power_kw: pd.Series
    bess_soc_kwh: pd.Series
    grid_power_kw: pd.Series
    config: EaSimConfig
    scenario_costs: Optional[pd.DataFrame] = None

    @property
    def battery_p_nom_kw(self) -> float:
        return self.config.battery_power_kw  # type: ignore[return-value]

    @property
    def battery_capacity_kwh(self) -> float:
        return self.config.battery_capacity_kwh

    def export_to_csv_folder(self, path: Path) -> None:
        """Export in PyPSA-compatible CSV format for visualization compatibility."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        idx = self.snapshots

        pd.DataFrame({"snapshot": idx}).to_csv(path / "snapshots.csv", index=False)

        loads = pd.DataFrame(
            {"demand": np.asarray(self.consumption_kw.values, dtype=float) / 1000.0},
            index=idx,
        )
        loads.index.name = "snapshot"
        loads.to_csv(path / "loads-p_set.csv")

        gen_data: dict[str, np.ndarray] = {
            "grid": np.asarray(self.grid_power_kw.values, dtype=float) / 1000.0
        }
        if float(self.pv_kw.abs().sum()) > 0:
            gen_data["pv"] = np.asarray(self.pv_kw.values, dtype=float) / 1000.0
        gen_p = pd.DataFrame(gen_data, index=idx)
        gen_p.index.name = "snapshot"
        gen_p.to_csv(path / "generators-p.csv")

        soc = pd.DataFrame(
            {"battery": np.asarray(self.bess_soc_kwh.values, dtype=float) / 1000.0},
            index=idx,
        )
        soc.index.name = "snapshot"
        soc.to_csv(path / "storage_units-state_of_charge.csv")

        charge_kw = np.maximum(0.0, np.asarray(self.bess_power_kw.values, dtype=float))
        store = pd.DataFrame({"battery": charge_kw / 1000.0}, index=idx)
        store.index.name = "snapshot"
        store.to_csv(path / "storage_units-p_store.csv")

        discharge_kw = np.maximum(
            0.0, -np.asarray(self.bess_power_kw.values, dtype=float)
        )
        dispatch = pd.DataFrame({"battery": discharge_kw / 1000.0}, index=idx)
        dispatch.index.name = "snapshot"
        dispatch.to_csv(path / "storage_units-p_dispatch.csv")

        detail = pd.DataFrame({
            "timestamp": idx,
            "consumption_kw": self.consumption_kw.values,
            "pv_kw": self.pv_kw.values,
            "net_power_kw": self.net_power_kw.values,
            "bess_power_kw": self.bess_power_kw.values,
            "bess_soc_kwh": self.bess_soc_kwh.values,
            "grid_power_kw": self.grid_power_kw.values,
        })
        detail.to_csv(path / "ea_sim_detail.csv", index=False)

        if self.scenario_costs is not None:
            self.scenario_costs.to_csv(
                path / "ea_sim_distribution_costs.csv", index=False
            )


# ---------------------------------------------------------------------------
# Distribution-cost post-processing (calculation models)
# ---------------------------------------------------------------------------

def compute_distribution_scenario_costs(
    result: EaSimResult,
    ea_config: EaSimConfig,
    calculator_names: Optional[list[str]] = None,
    log_to_console: bool = False,
) -> pd.DataFrame:
    """Compute distribution costs for multiple scenarios using one or more calculators.

    This is intentionally separated from the physical BESS simulation so that
    different tariff models (e.g. Belgian general, static QV, or future EL
    internal models) can be run as a distinct pipeline step on top of the
    simulated power profiles.
    """
    snapshots = result.snapshots
    steps_per_hour = 60.0 / float(result.interval_minutes)

    scenarios = {
        "baseline": result.consumption_kw,
        "with_pv": result.net_power_kw,
        "with_pv_bess": result.grid_power_kw,
    }
    calc_names = calculator_names or available_calculators()
    cost_frames: list[pd.DataFrame] = []

    if log_to_console:
        print("  Computing distribution costs …")

    for calc_name in calc_names:
        for scenario_name, power in scenarios.items():
            costs = _compute_monthly_distribution_costs(
                power,
                snapshots,
                ea_config.connection_capacity_kw,
                steps_per_hour,
                ea_config,
                calculator_name=calc_name,
            )
            costs.insert(0, "scenario", scenario_name)
            costs.insert(0, "calculator", calc_name)
            cost_frames.append(costs)

    scenario_costs = pd.concat(cost_frames, ignore_index=True)

    if log_to_console:
        for calc_name in calc_names:
            print(f"    [{calc_name}]")
            mask_calc = scenario_costs["calculator"] == calc_name
            for scenario_name in scenarios:
                mask = mask_calc & (scenario_costs["scenario"] == scenario_name)
                total = scenario_costs.loc[mask, "total_cost_eur"].sum()
                print(f"      {scenario_name}: €{total:,.2f}/year")

    return scenario_costs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def simulate(
    profile: ConsumptionProfile,
    ea_config: EaSimConfig,
    pv_profile: Optional[ConsumptionProfile] = None,
    log_to_console: bool = False,
    calculator_names: Optional[list[str]] = None,
) -> tuple[EaSimResult, str]:
    """Run EnergyLabs BESS simulation.

    Args:
        profile: Normalized consumption profile (power in kW).
        ea_config: EA simulation configuration.
        pv_profile: Optional PV generation profile (power in kW).
        log_to_console: Print progress to console.
        calculator_names: Distribution cost calculators to run. *None* = all
            registered calculators.

    Returns:
        (EaSimResult, status_string)
    """
    consumption_series = profile.power_kw
    snapshots = pd.DatetimeIndex(consumption_series.index)
    interval_minutes = profile.interval_minutes
    steps_per_hour = 60.0 / interval_minutes

    consumption_kw = consumption_series.values.astype(float)

    if pv_profile is not None:
        pv_series = pv_profile.power_kw
        pv_kw = pv_series.reindex(snapshots).ffill().fillna(0).values.astype(float)
    else:
        pv_kw = np.zeros_like(consumption_kw)

    net_power_kw = consumption_kw - pv_kw

    if log_to_console:
        print(f"EA Sim: {len(snapshots)} timesteps, {interval_minutes}min interval")
        print(f"  Battery: {ea_config.battery_capacity_kwh} kWh / {ea_config.battery_power_kw} kW")
        print(f"  Strategy: {ea_config.strategy}")
        print(f"  High-power threshold: {ea_config.high_power_threshold_kw} kW")
        if ea_config.strategy == "peak_shaving" and ea_config.off_peak_battery:
            print(f"  Off-peak pre-charge: {ea_config.off_peak_start_hour}:00–{ea_config.off_peak_end_hour}:00 (buffer from previous day peaks)")
        print(f"  Peak consumption: {consumption_kw.max():.1f} kW")
        print(f"  Peak PV: {pv_kw.max():.1f} kW")
        print(f"  Peak net power: {net_power_kw.max():.1f} kW")

    if ea_config.strategy == "pv_self_consumption":
        bess_power, bess_soc = _simulate_bess_pv_self_consumption(
            net_power_kw,
            ea_config.battery_capacity_kwh,
            ea_config.battery_power_kw,  # type: ignore[arg-type]
            steps_per_hour,
        )
    elif ea_config.strategy == "peak_shaving":
        bess_power, bess_soc = _simulate_bess_peak_shaving(
            net_power_kw,
            ea_config.battery_capacity_kwh,
            ea_config.battery_power_kw,  # type: ignore[arg-type]
            ea_config.high_power_threshold_kw,  # type: ignore[arg-type]
            steps_per_hour,
            snapshots=snapshots,
            off_peak_battery=ea_config.off_peak_battery,
            off_peak_start_hour=ea_config.off_peak_start_hour,
            off_peak_end_hour=ea_config.off_peak_end_hour,
        )
    else:
        raise ValueError(f"Unknown ea_sim strategy: {ea_config.strategy!r}")

    grid_power_kw = net_power_kw + bess_power

    if log_to_console:
        print(f"  Peak grid (after BESS): {grid_power_kw.max():.1f} kW")
        print(f"  Max SoC: {bess_soc.max():.1f} kWh")
        total_charged = float(np.sum(np.maximum(0.0, bess_power)) / steps_per_hour)
        total_discharged = float(np.sum(np.maximum(0.0, -bess_power)) / steps_per_hour)
        print(f"  Total charged: {total_charged:,.0f} kWh, discharged: {total_discharged:,.0f} kWh")

    consumption_s = pd.Series(consumption_kw, index=snapshots, name="consumption_kw")
    pv_s = pd.Series(pv_kw, index=snapshots, name="pv_kw")
    net_s = pd.Series(net_power_kw, index=snapshots, name="net_power_kw")
    bess_p_s = pd.Series(bess_power, index=snapshots, name="bess_power_kw")
    bess_soc_s = pd.Series(bess_soc, index=snapshots, name="bess_soc_kwh")
    grid_s = pd.Series(grid_power_kw, index=snapshots, name="grid_power_kw")

    result = EaSimResult(
        snapshots=snapshots,
        interval_minutes=interval_minutes,
        consumption_kw=consumption_s,
        pv_kw=pv_s,
        net_power_kw=net_s,
        bess_power_kw=bess_p_s,
        bess_soc_kwh=bess_soc_s,
        grid_power_kw=grid_s,
        config=ea_config,
        scenario_costs=None,
    )
    return result, "ok"
