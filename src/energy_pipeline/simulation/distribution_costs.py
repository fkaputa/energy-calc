"""Belgian distribution cost calculators.

Supports multiple calculation logics (calculators) for Belgian grid
distribution costs, selectable via configuration.

Analyst formula reference (March 2026)
======================================

Variables:
    X = Consumption / offtake (MWh)
    Y = Peak power (MW)
    A = Access power / connection capacity (MW)
    I = Injection (MWh)
    D = Days in month

Region-dependent coefficients:
    E = Elia transmission fraction (%)
    V = Connection price (€/MW/year)
    P = Peak power price (€/MW/year)
    O = Public service obligations / ODV (€/MWh)  [non-stationary only]
    T = Surcharges / Toeslagen (€/MWh)            [non-stationary only]

Fixed coefficients (same across regions):
    Offtake base   = 29.14 €/MWh  (taxes 12.09 + certificates 15.2 + losses 1.85)
    Injection rate = 1.751 €/MWh
    Fixed daily    = 7.2896 €/day (data mgmt 0.316 + energy fund 6.316 + admin 0.6576)
    Cap            = 150.8082 €/MWh (max tariff excl. taxes & green certificates)
    Stationary discount = 0.8 (80% on Elia transmission part)

Non-stationary BESS:
    Z = min(150.8082·X, A·V·D/365 + Y·P·D/365 + (O+T)·X) + 29.14·X + 1.751·I + 7.2896·D

Stationary BESS:
    Z = min(150.8082·X, A·V·(1−0.8·E)·D/365 + Y·P·(1−0.8·E)·D/365) + 29.14·X + 1.751·I + 7.2896·D

Relative cost:
    G(Z) = Z / X  (€/MWh)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

@dataclass
class DistCostParams:
    """All coefficients for Belgian distribution cost calculation."""

    is_stationary: bool = False

    # Region-dependent coefficients
    elia_fraction: float = 0.0
    connection_price_eur_per_mw: float = 40_684.4988  # V
    peak_price_eur_per_mw: float = 59_856.96           # P
    odv_eur_per_mwh: float = 3.9196                    # O (non-stationary only)
    surcharge_eur_per_mwh: float = 0.3058              # T (non-stationary only)

    # Fixed coefficients (same across all regions)
    offtake_base_eur_per_mwh: float = 29.14
    injection_eur_per_mwh: float = 1.751
    fixed_daily_eur: float = 7.2896
    capacity_cap_eur_per_mwh: float = 150.8082
    stationary_discount: float = 0.8


# ---------------------------------------------------------------------------
# Calculator base
# ---------------------------------------------------------------------------

class DistCostCalculator(ABC):
    """Base class for distribution cost calculators."""

    @abstractmethod
    def compute_month(
        self,
        X: float,
        Y: float,
        A: float,
        I: float,
        D: int,
    ) -> Dict[str, float]:
        """Compute distribution cost for a single month.

        Parameters
        ----------
        X : Offtake / consumption (MWh)
        Y : Peak power (MW)
        A : Access / connection capacity (MW)
        I : Injection (MWh)
        D : Days in the month
        """

    def compute_monthly_from_timeseries(
        self,
        power_kw: pd.Series,
        snapshots: pd.DatetimeIndex,
        connection_kw: float,
        steps_per_hour: float,
    ) -> pd.DataFrame:
        """Compute monthly costs from a full timeseries of grid power (kW)."""
        df = pd.DataFrame({"power_kw": power_kw.values, "timestamp": snapshots})
        df["month"] = df["timestamp"].dt.to_period("M")

        A = connection_kw / 1000.0

        records = []
        for month, grp in df.groupby("month"):
            offtake_kw = grp["power_kw"].clip(lower=0)
            injection_kw = (-grp["power_kw"]).clip(lower=0)

            X = offtake_kw.sum() / steps_per_hour / 1000.0
            Y = offtake_kw.max() / 1000.0
            I_mwh = injection_kw.sum() / steps_per_hour / 1000.0
            D = grp["timestamp"].dt.date.nunique()

            row = self.compute_month(X, Y, A, I_mwh, D)
            row["month"] = str(month)
            row["offtake_mwh"] = round(X, 3)
            row["peak_mw"] = round(Y, 3)
            row["injection_mwh"] = round(I_mwh, 3)
            row["days"] = D
            records.append(row)

        result = pd.DataFrame(records)
        cols_front = ["month", "offtake_mwh", "peak_mw", "injection_mwh", "days"]
        other = [c for c in result.columns if c not in cols_front]
        return result[cols_front + other]


# ---------------------------------------------------------------------------
# Belgian General Calculator (analyst formula March 2026)
# ---------------------------------------------------------------------------

class BelgianGeneralCalculator(DistCostCalculator):
    """Analyst formula for Belgian distribution costs.

    Supports both stationary and non-stationary BESS, with region-dependent
    coefficients (V, P, O, T, E) and a configurable cap.
    """

    def __init__(self, params: DistCostParams) -> None:
        self.params = params

    def compute_month(
        self,
        X: float,
        Y: float,
        A: float,
        I: float,
        D: int,
    ) -> Dict[str, float]:
        p = self.params

        if p.is_stationary:
            discount = 1.0 - p.stationary_discount * p.elia_fraction
            access_cost = A * p.connection_price_eur_per_mw * discount * D / 365
            peak_cost = Y * p.peak_price_eur_per_mw * discount * D / 365
            odv_surcharge_cost = 0.0
        else:
            access_cost = A * p.connection_price_eur_per_mw * D / 365
            peak_cost = Y * p.peak_price_eur_per_mw * D / 365
            odv_surcharge_cost = (p.odv_eur_per_mwh + p.surcharge_eur_per_mwh) * X

        uncapped = access_cost + peak_cost + odv_surcharge_cost
        cap = p.capacity_cap_eur_per_mwh * X
        capped_cost = min(cap, uncapped)
        cap_applied = capped_cost < uncapped

        offtake_base_cost = p.offtake_base_eur_per_mwh * X
        injection_cost = p.injection_eur_per_mwh * I
        fixed_cost = p.fixed_daily_eur * D

        total = capped_cost + offtake_base_cost + injection_cost + fixed_cost
        relative = total / X if X > 0 else 0.0

        return {
            "access_cost_eur": round(access_cost, 2),
            "peak_cost_eur": round(peak_cost, 2),
            "odv_surcharge_cost_eur": round(odv_surcharge_cost, 2),
            "uncapped_grid_cost_eur": round(uncapped, 2),
            "capped_grid_cost_eur": round(capped_cost, 2),
            "cap_applied": 1 if cap_applied else 0,
            "offtake_base_cost_eur": round(offtake_base_cost, 2),
            "injection_cost_eur": round(injection_cost, 2),
            "fixed_cost_eur": round(fixed_cost, 2),
            "total_cost_eur": round(total, 2),
            "relative_cost_eur_per_mwh": round(relative, 4),
        }


# ---------------------------------------------------------------------------
# Static QV Calculator (pre-March 2026 implementation)
# ---------------------------------------------------------------------------

class StaticQvCalculator(DistCostCalculator):
    """Original simplified 2026 formula (backward compatibility).

    Structure:
        min(cap·X, A·V·D/365 + Y·P·D/365 + surcharge_daily·D)
        + (O + T + offtake_base) · X
        + injection · I
        + fixed_daily · D

    Does not distinguish stationary / non-stationary.
    """

    def __init__(self, params: DistCostParams) -> None:
        self.params = params
        self._daily_capacity_surcharge: float = 0.155

    def compute_month(
        self,
        X: float,
        Y: float,
        A: float,
        I: float,
        D: int,
    ) -> Dict[str, float]:
        p = self.params

        access_cost = A * p.connection_price_eur_per_mw * D / 365
        peak_cost = Y * p.peak_price_eur_per_mw * D / 365

        uncapped = access_cost + peak_cost + self._daily_capacity_surcharge * D
        cap = p.capacity_cap_eur_per_mwh * X
        capped_cost = min(cap, uncapped)

        variable_cost = (
            p.odv_eur_per_mwh
            + p.surcharge_eur_per_mwh
            + p.offtake_base_eur_per_mwh
        ) * X
        injection_cost = p.injection_eur_per_mwh * I
        fixed_cost = D * 7.1316

        total = capped_cost + variable_cost + injection_cost + fixed_cost
        relative = total / X if X > 0 else 0.0

        return {
            "access_cost_eur": round(access_cost, 2),
            "peak_cost_eur": round(peak_cost, 2),
            "odv_surcharge_cost_eur": 0.0,
            "uncapped_grid_cost_eur": round(uncapped, 2),
            "capped_grid_cost_eur": round(capped_cost, 2),
            "cap_applied": 1 if capped_cost < uncapped else 0,
            "offtake_base_cost_eur": round(variable_cost, 2),
            "injection_cost_eur": round(injection_cost, 2),
            "fixed_cost_eur": round(fixed_cost, 2),
            "total_cost_eur": round(total, 2),
            "relative_cost_eur_per_mwh": round(relative, 4),
        }


# ---------------------------------------------------------------------------
# Calculator registry
# ---------------------------------------------------------------------------

_CALCULATORS: Dict[str, type] = {
    "belgian_general": BelgianGeneralCalculator,
    "static_qv": StaticQvCalculator,
}


def available_calculators() -> list[str]:
    """Return names of all registered distribution cost calculators."""
    return list(_CALCULATORS.keys())


def get_calculator(
    name: str,
    params: Optional[DistCostParams] = None,
) -> DistCostCalculator:
    """Instantiate a distribution cost calculator by name.

    Parameters
    ----------
    name : Calculator identifier (e.g. ``"belgian_general"``).
    params : Cost parameters; defaults to ``DistCostParams()`` if *None*.
    """
    if name not in _CALCULATORS:
        raise ValueError(
            f"Unknown distribution cost calculator {name!r}. "
            f"Available: {available_calculators()}"
        )
    if params is None:
        params = DistCostParams()
    return _CALCULATORS[name](params)
