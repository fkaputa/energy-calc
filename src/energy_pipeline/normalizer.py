"""Resampling and unit conversion for consumption profiles."""

from typing import List, Union

import pandas as pd

from .schema import ConsumptionProfile, NormalizedConfig


def aggregate_profiles(profiles: List[ConsumptionProfile], source_identifier: str = "aggregated") -> ConsumptionProfile:
    """Combine multiple profiles by summing power_kw per timestamp (missing = 0)."""
    if not profiles:
        raise ValueError("Need at least one profile to aggregate")
    if len(profiles) == 1:
        return profiles[0]
    all_series = [p.power_kw for p in profiles]
    combined = pd.concat(all_series, axis=1).fillna(0).sum(axis=1)
    combined = combined.loc[~combined.index.duplicated(keep="first")]
    df = pd.DataFrame({"timestamp": combined.index, "power_kw": combined.values})
    interval = profiles[0].interval_minutes
    return ConsumptionProfile(data=df, source_identifier=source_identifier, interval_minutes=interval)


def resample_profile(
    profile: ConsumptionProfile,
    rule: str = "15min",
    aggregation: str = "mean",
) -> ConsumptionProfile:
    """Resample consumption profile to target resolution.

    Args:
        profile: Input consumption profile
        rule: Pandas resample rule (e.g. '15min', '1H')
        aggregation: 'mean' for average power, 'sum' for total energy

    Returns:
        New ConsumptionProfile with resampled data
    """
    s = profile.power_kw
    s = s[~s.index.duplicated(keep="first")]
    s = s.sort_index()
    if aggregation == "mean":
        resampled = s.resample(rule).mean()
    elif aggregation == "sum":
        resampled = s.resample(rule).sum()
        # Convert energy (kWh) back to average power (kW) over interval
        hours = pd.Timedelta(rule).total_seconds() / 3600
        resampled = resampled / hours
    else:
        raise ValueError("aggregation must be 'mean' or 'sum'")
    resampled = resampled.dropna()
    df = pd.DataFrame({"timestamp": resampled.index, "power_kw": resampled.values})
    interval_min = int(pd.Timedelta(rule).total_seconds() / 60)
    return ConsumptionProfile(
        data=df,
        source_identifier=profile.source_identifier,
        interval_minutes=interval_min,
    )


def to_pypsa_load_series(profile: ConsumptionProfile) -> pd.Series:
    """Convert profile to PyPSA load p_set format: datetime index, values in MW."""
    return profile.to_series()


def project_pv_to_consumption_dates(
    pv_profile: ConsumptionProfile,
    consumption_profile: ConsumptionProfile,
) -> tuple[ConsumptionProfile, bool]:
    """Extend PV profile to cover consumption timestamps by copying the last available year.

    PV data (e.g. from PVGIS) often ends in 2023 while consumption runs into 2025. This
    projects PV by reusing the pattern from the last full year in PV for any consumption
    timestamps that fall outside the PV range. So 2025 is simulated using 2023's pattern.

    Returns:
        (extended_pv_profile, was_projected) where was_projected is True if any fill was done.
    """
    pv_series = pv_profile.power_kw.sort_index()
    cons_ts = consumption_profile.timestamps
    if pv_series.empty or len(cons_ts) == 0:
        return pv_profile, False
    max_pv = pv_series.index.max()
    need = cons_ts[cons_ts > max_pv]
    if len(need) == 0:
        return pv_profile, False
    # Use last full year in PV as template (e.g. 2023)
    last_year = int(pv_series.index.year.max())
    template = pv_series[pv_series.index.year == last_year]
    if template.empty:
        return pv_profile, False
    # Map (month, day, hour) -> power so sub-hourly consumption gets same PV for the hour
    by_tod = template.groupby(
        [template.index.month, template.index.day, template.index.hour],
        as_index=True,
    ).mean()

    def lookup(ts: pd.Timestamp) -> float:
        key = (ts.month, ts.day, ts.hour)
        if key in by_tod.index:
            return float(by_tod.loc[key])
        if ts.month == 2 and ts.day == 29:
            key = (2, 28, ts.hour)
        if key in by_tod.index:
            return float(by_tod.loc[key])
        return 0.0

    # Full index = PV timestamps + consumption timestamps (consumption may be 15-min, PV hourly)
    combined_index = pv_series.index.union(cons_ts).drop_duplicates().sort_values()
    extended = pv_series.reindex(combined_index)
    for ts in combined_index:
        if ts > max_pv:
            extended.loc[ts] = lookup(ts)
    extended = extended.ffill().bfill().fillna(0)
    # Align to consumption: reindex to cons_ts
    aligned = extended.reindex(cons_ts).ffill().bfill().fillna(0)
    df = pd.DataFrame({"timestamp": aligned.index, "power_kw": aligned.values})
    out = ConsumptionProfile(
        data=df,
        source_identifier=pv_profile.source_identifier + "_projected",
        interval_minutes=pv_profile.interval_minutes,
    )
    return out, True
