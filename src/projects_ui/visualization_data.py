"""Load pipeline output and build HTML tables + chart data for inline visualization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _read_csv_safe(path: Path, **kwargs: Any) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, **kwargs)
    except Exception:
        return None


def _trim_to_days(obj: pd.DataFrame | pd.Series, ts_index: pd.DatetimeIndex | None, days: int = 14) -> pd.DataFrame | pd.Series:
    if obj is None or (isinstance(obj, pd.DataFrame) and obj.empty) or (isinstance(obj, pd.Series) and obj.empty):
        return obj if obj is not None else (pd.DataFrame() if not isinstance(obj, pd.Series) else pd.Series(dtype=float))
    if days <= 0:
        return obj
    index = obj.index
    if isinstance(index, pd.DatetimeIndex) and len(index) > 0:
        cutoff = index.min() + pd.Timedelta(days=days)
        return obj.loc[obj.index <= cutoff]
    if ts_index is not None and len(ts_index) > 0:
        cutoff = ts_index.min() + pd.Timedelta(days=days)
        n = (ts_index <= cutoff).sum()
        return obj.iloc[: min(n, len(obj))]
    return obj


def load_visualization_data(
    output_dir: Path,
    chart_days: int = 14,
) -> dict[str, Any]:
    """Load all result CSVs and build context for inline HTML visualization.

    Returns dict with:
      - summary: { peak_kw, mean_kw, total_mwh, n_snapshots, date_range }
      - tables: [ { title, html }, ... ]
      - chart_data: { consumption, grid_demand, soc, marginal_price, ... } with labels + datasets
      - has_pypsa, has_ea_sim_distribution
    """
    out = Path(output_dir)
    result: dict[str, Any] = {
        "summary": None,
        "tables": [],
        "chart_data": {},
        "has_pypsa": False,
        "has_ea_sim_distribution": False,
    }

    consumption = _read_csv_safe(out / "consumption_profile.csv", parse_dates=["timestamp"])
    pv = _read_csv_safe(out / "pv_generation_profile.csv", parse_dates=["timestamp"])
    ts_full = pd.DatetimeIndex([])
    if consumption is not None and not consumption.empty:
        ts_full = pd.DatetimeIndex(consumption["timestamp"])

    # PyPSA results
    pypsa_dir = out / "pypsa_results"
    snapshots = _read_csv_safe(pypsa_dir / "snapshots.csv", parse_dates=["snapshot"])
    if snapshots is not None and not snapshots.empty:
        ts_full = pd.DatetimeIndex(snapshots["snapshot"])
        result["has_pypsa"] = True

    loads_df = None
    if (pypsa_dir / "loads-p_set.csv").exists():
        lp = pd.read_csv(pypsa_dir / "loads-p_set.csv")
        skip = {"Unnamed: 0", "snapshot"}
        cols = [c for c in lp.columns if c not in skip]
        if cols and len(ts_full) >= len(lp):
            loads_df = pd.DataFrame(
                lp[cols].values,
                index=ts_full[: len(lp)],
                columns=cols,
            )

    gen_p = _read_csv_safe(pypsa_dir / "generators-p.csv", index_col=0, parse_dates=True)
    if gen_p is not None and not isinstance(gen_p.index, pd.DatetimeIndex) and len(ts_full) == len(gen_p):
        gen_p.index = ts_full[: len(gen_p)]

    soc = _read_csv_safe(pypsa_dir / "storage_units-state_of_charge.csv", index_col=0, parse_dates=True)
    if soc is not None and not isinstance(soc.index, pd.DatetimeIndex) and len(ts_full) == len(soc):
        soc.index = ts_full[: len(soc)]

    marginal_price = _read_csv_safe(pypsa_dir / "buses-marginal_price.csv", index_col=0, parse_dates=True)
    if marginal_price is not None and not isinstance(marginal_price.index, pd.DatetimeIndex) and len(ts_full) == len(marginal_price):
        marginal_price.index = ts_full[: len(marginal_price)]

    # Summary
    if consumption is not None and not consumption.empty:
        pw = consumption["power_kw"]
        interval_h = (consumption["timestamp"].diff().dt.total_seconds().median() or 900) / 3600
        total_mwh = float((pw * interval_h).sum() / 1000)
        result["summary"] = {
            "peak_kw": float(pw.max()),
            "mean_kw": float(pw.mean()),
            "total_mwh": total_mwh,
            "n_snapshots": len(consumption),
            "date_range": f"{consumption['timestamp'].min()} — {consumption['timestamp'].max()}",
        }
    elif loads_df is not None and "demand" in loads_df.columns:
        d = loads_df["demand"] * 1000  # MW -> kW
        result["summary"] = {
            "peak_kw": float(d.max()),
            "mean_kw": float(d.mean()),
            "total_mwh": float(d.sum() * (ts_full[1] - ts_full[0]).total_seconds() / 3600 / 1000) if len(ts_full) > 1 else 0,
            "n_snapshots": len(loads_df),
            "date_range": f"{ts_full.min()} — {ts_full.max()}",
        }

    # Tables
    table_css = "viz-table"
    if consumption is not None and not consumption.empty:
        preview = consumption.head(500)
        preview = preview.copy()
        if "timestamp" in preview.columns:
            preview["timestamp"] = pd.to_datetime(preview["timestamp"]).dt.strftime("%Y-%m-%d %H:%M")
        result["tables"].append({
            "title": "Verbruik (preview)",
            "html": preview.to_html(classes=table_css, index=False),
        })

    if gen_p is not None and not gen_p.empty:
        df = gen_p.head(200).copy()
        df.index = df.index.astype(str)
        result["tables"].append({
            "title": "Generator dispatch (MW)",
            "html": df.to_html(classes=table_css),
        })

    if soc is not None and not soc.empty:
        df = soc.head(200).copy()
        df.index = df.index.astype(str)
        result["tables"].append({
            "title": "Batterij state of charge (MWh)",
            "html": df.to_html(classes=table_css),
        })

    dist_path = pypsa_dir / "ea_sim_distribution_costs.csv"
    if dist_path.exists():
        dist = pd.read_csv(dist_path)
        result["has_ea_sim_distribution"] = True
        # Summary per scenario (year total)
        scenarios = ["baseline", "with_pv", "with_pv_bess"]
        calc_col = "calculator" if "calculator" in dist.columns else None
        calculators = dist[calc_col].unique().tolist() if calc_col and calc_col in dist.columns else []
        if not calculators:
            calculators = ["default"]
        for calc_name in calculators[:1]:  # first calculator summary table
            sub = dist[dist[calc_col] == calc_name] if calc_col and calc_col in dist.columns else dist
            agg = sub.groupby("scenario", as_index=False).agg({
                "offtake_mwh": "sum",
                "peak_mw": "max",
                "total_cost_eur": "sum",
                "capped_grid_cost_eur": "sum",
                "offtake_base_cost_eur": "sum",
                "injection_cost_eur": "sum",
                "fixed_cost_eur": "sum",
            })
            agg = agg.round(2)
            result["tables"].append({
                "title": "Distributiekosten (jaaroverzicht)",
                "html": agg.to_html(classes=table_css, index=False),
            })
        # Monthly table (first calculator, sample)
        if len(calculators) > 0:
            sub = dist[dist[calc_col] == calculators[0]] if (calc_col and calc_col in dist.columns) else dist
            sort_cols = [c for c in ["scenario", "month"] if c in sub.columns]
            if sort_cols:
                sub = sub.sort_values(sort_cols).head(36)
            cols_show = [c for c in ["scenario", "month", "offtake_mwh", "peak_mw", "total_cost_eur"] if c in sub.columns]
            if cols_show:
                result["tables"].append({
                    "title": "Distributiekosten per maand (preview)",
                    "html": sub[cols_show].to_html(classes=table_css, index=False),
                })

    # Chart data (sampled for ~chart_days)
    def _series_to_chart(series: pd.Series, days: int) -> dict[str, Any]:
        if series is None or series.empty:
            return {"labels": [], "datasets": []}
        trimmed = _trim_to_days(series, None, days)
        if trimmed.empty:
            return {"labels": [], "datasets": []}
        idx = trimmed.index.astype(str).tolist()
        # Allow much finer granularity for CSI-style exploration in the web UI.
        max_points = 5000
        if len(idx) > max_points:
            step = len(idx) // max_points
            trimmed = trimmed.iloc[:: step]
            idx = trimmed.index.astype(str).tolist()
        return {
            "labels": idx,
            "datasets": [{"label": series.name or "value", "data": trimmed.fillna(0).tolist()}],
        }

    def _df_to_chart(df: pd.DataFrame, days: int, scale: float = 1.0) -> dict[str, Any]:
        if df is None or df.empty:
            return {"labels": [], "datasets": []}
        trimmed = _trim_to_days(df, df.index if isinstance(df.index, pd.DatetimeIndex) else None, days)
        if trimmed.empty:
            return {"labels": [], "datasets": []}
        idx = trimmed.index.astype(str).tolist()
        # Allow much finer granularity for CSI-style exploration in the web UI.
        max_points = 5000
        if len(idx) > max_points:
            step = len(idx) // max_points
            trimmed = trimmed.iloc[:: step]
            idx = trimmed.index.astype(str).tolist()
        datasets = []
        for col in trimmed.columns:
            datasets.append({
                "label": col,
                "data": (trimmed[col].fillna(0) * scale).tolist(),
            })
        return {"labels": idx, "datasets": datasets}

    if consumption is not None and not consumption.empty:
        c = consumption.set_index("timestamp")["power_kw"]
        result["chart_data"]["consumption"] = _series_to_chart(c, chart_days)

    if loads_df is not None and "demand" in loads_df.columns:
        result["chart_data"]["demand_kw"] = _df_to_chart(
            loads_df[["demand"]].rename(columns={"demand": "Vraag (kW)"}) * 1000,
            chart_days,
        )

    if gen_p is not None and not gen_p.empty:
        result["chart_data"]["generators_mw"] = _df_to_chart(gen_p, chart_days)

    if soc is not None and not soc.empty:
        result["chart_data"]["soc_kwh"] = _df_to_chart(soc * 1000, chart_days)  # MWh -> kWh

    if marginal_price is not None and not marginal_price.empty:
        result["chart_data"]["marginal_price"] = _df_to_chart(marginal_price, chart_days)

    if pv is not None and not pv.empty:
        pv_ts = pv.set_index("timestamp")["power_kw"]
        result["chart_data"]["pv_kw"] = _series_to_chart(pv_ts, chart_days)

    return result
