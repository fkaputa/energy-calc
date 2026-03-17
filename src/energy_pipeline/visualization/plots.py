"""Generate PDF and PNG plots from PyPSA and consumption results."""

from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd


def _load_results(output_dir: Path) -> tuple[
    Optional[pd.DataFrame],
    Optional[pd.DataFrame],
    Optional[pd.DataFrame],
    Optional[pd.DataFrame],
    Optional[pd.DataFrame],
    pd.DatetimeIndex,
    Optional[pd.DataFrame],
]:
    """Load consumption profile, PV profile (if any), and PyPSA result CSVs.
    Returns: (consumption, loads, gen_p, soc, marginal_price, timestamps, pv).
    """
    out = Path(output_dir)
    consumption = None
    loads = None
    gen_p = None
    soc = None
    marginal_price = None
    pv = None
    ts = pd.DatetimeIndex([])

    # Consumption profile
    cp = out / "consumption_profile.csv"
    if cp.exists():
        consumption = pd.read_csv(cp, parse_dates=["timestamp"])
        ts = pd.DatetimeIndex(consumption["timestamp"])

    # PV generation profile (optional)
    pv_path = out / "pv_generation_profile.csv"
    if pv_path.exists():
        pv = pd.read_csv(pv_path, parse_dates=["timestamp"])

    def _read_series_csv(csv_path: Path, ts: pd.DatetimeIndex) -> pd.DataFrame | None:
        """Read a PyPSA series CSV (with or without datetime index)."""
        if not csv_path.exists():
            return None
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        if df.empty:
            return None
        # Use snapshot order when CSV index is not datetime (e.g. 0,1,2 from export)
        if not isinstance(df.index, pd.DatetimeIndex) and len(df) == len(ts):
            df.index = ts[: len(df)]
        return df

    snapshots_path = out / "pypsa_results" / "snapshots.csv"
    if snapshots_path.exists():
        snapshots = pd.read_csv(snapshots_path, parse_dates=["snapshot"])
        ts = pd.DatetimeIndex(snapshots["snapshot"])
        # Loads (export often has no datetime column, use ts)
        lp = out / "pypsa_results" / "loads-p_set.csv"
        if lp.exists():
            df = pd.read_csv(lp)
            _skip = {"Unnamed: 0", "snapshot"}
            cols = [c for c in df.columns if c not in _skip]
            loads = pd.DataFrame(df[cols].values, index=ts[: len(df)], columns=cols)
        # Generator dispatch, SOC, marginal price (from pipeline export or PyPSA export)
        gen_p = _read_series_csv(out / "pypsa_results" / "generators-p.csv", ts)
        soc = _read_series_csv(out / "pypsa_results" / "storage_units-state_of_charge.csv", ts)
        marginal_price = _read_series_csv(out / "pypsa_results" / "buses-marginal_price.csv", ts)

    if consumption is None and loads is None:
        raise FileNotFoundError(f"No results found in {output_dir}")
    return consumption, loads, gen_p, soc, marginal_price, ts, pv


_SCENARIO_LABELS = {
    "baseline": "Baseline",
    "with_pv": "Met PV",
    "with_pv_bess": "Met PV + BESS",
}

_CALCULATOR_LABELS = {
    "belgian_general": "Analistenformule (stationair/non-stationair)",
    "static_qv": "Static QV",
}


def _fmt_eur(v: float) -> str:
    return f"€{v:,.2f}"


def _fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


def _render_distribution_cost_summary(
    df: pd.DataFrame,
    calculator_name: str,
) -> "plt.Figure":
    """Render a summary table with cost breakdown for one calculator."""
    import matplotlib.pyplot as plt

    calc_df = df[df["calculator"] == calculator_name] if "calculator" in df.columns else df
    scenarios = [s for s in ["baseline", "with_pv", "with_pv_bess"] if s in calc_df["scenario"].unique()]

    summary_rows = []
    totals = {}
    for scenario in scenarios:
        s = calc_df[calc_df["scenario"] == scenario]
        year_cost = s["total_cost_eur"].sum()
        year_offtake = s["offtake_mwh"].sum()
        peak = s["peak_mw"].max()
        capped = s["capped_grid_cost_eur"].sum()
        offtake_base = s["offtake_base_cost_eur"].sum()
        inj = s["injection_cost_eur"].sum()
        fixed = s["fixed_cost_eur"].sum()
        rel = year_cost / year_offtake if year_offtake > 0 else 0
        totals[scenario] = year_cost
        summary_rows.append([
            _SCENARIO_LABELS.get(scenario, scenario),
            f"{year_offtake:,.0f}",
            f"{peak:,.3f}",
            _fmt_eur(capped),
            _fmt_eur(offtake_base),
            _fmt_eur(inj),
            _fmt_eur(fixed),
            _fmt_eur(year_cost),
            f"€{rel:,.2f}",
        ])

    base_cost = totals.get("baseline", 0)
    for i, scenario in enumerate(scenarios):
        if scenario == "baseline":
            summary_rows[i].append("—")
        elif base_cost > 0:
            saving = base_cost - totals[scenario]
            summary_rows[i].append(f"{_fmt_eur(saving)} ({saving/base_cost*100:.1f}%)")
        else:
            summary_rows[i].append("—")

    col_labels = ["Scenario", "Afname\n(MWh)", "Piek\n(MW)",
                  "Grid kost\n(gecapt)", "Afname\nbasis", "Injectie\nkost",
                  "Vaste\nkost", "Jaarkost\n(EUR)", "Rel. kost\n(EUR/MWh)", "Besparing"]

    fig, ax = plt.subplots(figsize=(14, 1.2 + 0.45 * len(summary_rows)))
    ax.axis("off")

    calc_label = _CALCULATOR_LABELS.get(calculator_name, calculator_name)
    ax.set_title(f"Distributiekosten — {calc_label}", fontsize=13, fontweight="bold", pad=12)

    table = ax.table(
        cellText=summary_rows,
        colLabels=col_labels,
        loc="center",
        cellLoc="right",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.6)

    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#4472C4")
            cell.set_text_props(color="white", fontweight="bold")
            cell.set_edgecolor("white")
        else:
            cell.set_facecolor("#F2F2F2" if row % 2 == 0 else "white")
            cell.set_edgecolor("#D9D9D9")
        if col == 0:
            cell.set_text_props(ha="left")

    fig.tight_layout()
    return fig


def _render_calculator_comparison(df: pd.DataFrame) -> "plt.Figure":
    """Render a side-by-side comparison table of all calculators."""
    import matplotlib.pyplot as plt

    calculators = df["calculator"].unique() if "calculator" in df.columns else []
    if len(calculators) < 2:
        return None  # type: ignore[return-value]

    scenarios = [s for s in ["baseline", "with_pv", "with_pv_bess"] if s in df["scenario"].unique()]
    cost_cols = ["capped_grid_cost_eur", "offtake_base_cost_eur", "injection_cost_eur",
                 "fixed_cost_eur", "total_cost_eur"]
    col_short = ["Grid kost\n(gecapt)", "Afname\nbasis", "Injectie\nkost",
                 "Vaste\nkost", "Totaal"]

    rows = []
    for scenario in scenarios:
        label = _SCENARIO_LABELS.get(scenario, scenario)
        calc_totals = {}
        for calc_name in calculators:
            s = df[(df["calculator"] == calc_name) & (df["scenario"] == scenario)]
            calc_totals[calc_name] = {c: s[c].sum() for c in cost_cols}

        for calc_name in calculators:
            calc_label = _CALCULATOR_LABELS.get(calc_name, calc_name)
            row = [label, calc_label]
            for c in cost_cols:
                row.append(_fmt_eur(calc_totals[calc_name][c]))
            rows.append(row)

        delta_row = [label, "Δ (verschil)"]
        c0, c1 = list(calculators)[:2]
        for c in cost_cols:
            diff = calc_totals[c0][c] - calc_totals[c1][c]
            delta_row.append(f"{'+'if diff > 0 else ''}{_fmt_eur(diff)}")
        rows.append(delta_row)

    header = ["Scenario", "Calculator"] + col_short
    n_rows = len(rows)
    fig, ax = plt.subplots(figsize=(14, 1.5 + 0.35 * n_rows))
    ax.axis("off")
    ax.set_title("Vergelijking distributiekost-calculators", fontsize=13, fontweight="bold", pad=12)

    table = ax.table(cellText=rows, colLabels=header, loc="center", cellLoc="right")
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    table.scale(1.0, 1.4)

    scenario_colors = {"Baseline": "#E8F0FE", "Met PV": "#FFF3E0", "Met PV + BESS": "#E8F5E9"}
    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#4472C4")
            cell.set_text_props(color="white", fontweight="bold")
            cell.set_edgecolor("white")
        else:
            data_row = rows[row - 1]
            sc_label = data_row[0]
            is_delta = data_row[1] == "Δ (verschil)"
            bg = scenario_colors.get(sc_label, "#FFFFFF")
            cell.set_facecolor(bg)
            cell.set_edgecolor("#D9D9D9")
            if is_delta:
                cell.set_text_props(fontweight="bold", fontstyle="italic")
        if col <= 1:
            cell.set_text_props(ha="left")

    fig.tight_layout()
    return fig


def _render_distribution_cost_monthly(
    df: pd.DataFrame,
    calculator_name: str,
) -> "plt.Figure":
    """Render monthly breakdown table for one calculator."""
    import matplotlib.pyplot as plt

    calc_df = df[df["calculator"] == calculator_name] if "calculator" in df.columns else df
    scenarios = [s for s in ["baseline", "with_pv", "with_pv_bess"] if s in calc_df["scenario"].unique()]

    rows = []
    for scenario in scenarios:
        s = calc_df[calc_df["scenario"] == scenario].sort_values("month")
        for _, r in s.iterrows():
            rows.append([
                _SCENARIO_LABELS.get(scenario, scenario),
                r["month"],
                f"{r['offtake_mwh']:,.1f}",
                f"{r['peak_mw']:,.3f}",
                _fmt_eur(r["capped_grid_cost_eur"]),
                _fmt_eur(r["offtake_base_cost_eur"]),
                _fmt_eur(r["injection_cost_eur"]),
                _fmt_eur(r["fixed_cost_eur"]),
                _fmt_eur(r["total_cost_eur"]),
                f"€{r['relative_cost_eur_per_mwh']:,.2f}",
            ])
        year_total = s["total_cost_eur"].sum()
        year_offtake = s["offtake_mwh"].sum()
        rel = year_total / year_offtake if year_offtake > 0 else 0
        rows.append([
            _SCENARIO_LABELS.get(scenario, scenario),
            "TOTAAL",
            f"{year_offtake:,.1f}",
            f"{s['peak_mw'].max():,.3f}",
            _fmt_eur(s["capped_grid_cost_eur"].sum()),
            _fmt_eur(s["offtake_base_cost_eur"].sum()),
            _fmt_eur(s["injection_cost_eur"].sum()),
            _fmt_eur(s["fixed_cost_eur"].sum()),
            _fmt_eur(year_total),
            f"€{rel:,.2f}",
        ])

    col_labels = ["Scenario", "Maand", "Afname\n(MWh)", "Piek\n(MW)",
                  "Grid kost\n(gecapt)", "Afname\nbasis", "Injectie\nkost",
                  "Vaste\nkost", "Totaal\n(EUR)", "Rel.\n(EUR/MWh)"]

    n_rows = len(rows)
    fig_height = max(4, 1.5 + 0.28 * n_rows)
    fig, ax = plt.subplots(figsize=(14, fig_height))
    ax.axis("off")

    calc_label = _CALCULATOR_LABELS.get(calculator_name, calculator_name)
    ax.set_title(f"Distributiekosten per maand — {calc_label}", fontsize=12, fontweight="bold", pad=10)

    table = ax.table(
        cellText=rows,
        colLabels=col_labels,
        loc="center",
        cellLoc="right",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7)
    table.scale(1.0, 1.25)

    scenario_idx = 0
    colors_per_scenario = ["#E8F0FE", "#FFF3E0", "#E8F5E9"]
    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#4472C4")
            cell.set_text_props(color="white", fontweight="bold", fontsize=7)
            cell.set_edgecolor("white")
        else:
            data_row = rows[row - 1]
            is_total = data_row[1] == "TOTAAL"
            sc_name = data_row[0]
            sc_idx = list(_SCENARIO_LABELS.values()).index(sc_name) if sc_name in _SCENARIO_LABELS.values() else 0
            bg = colors_per_scenario[sc_idx % len(colors_per_scenario)]
            cell.set_facecolor(bg)
            cell.set_edgecolor("#D9D9D9")
            if is_total:
                cell.set_text_props(fontweight="bold")
        if col == 0 or col == 1:
            cell.set_text_props(ha="left")

    fig.tight_layout()
    return fig


def visualize_results(
    output_dir: Union[str, Path],
    images_dir: Optional[Union[str, Path]] = None,
    pdf_path: Optional[Union[str, Path]] = None,
    sample_days: Optional[int] = None,
) -> list[Path]:
    """Generate visualization plots as PNG and optionally PDF.

    Args:
        output_dir: Directory containing consumption_profile.csv and pypsa_results/
        images_dir: Directory for PNG images (default: output_dir/images)
        pdf_path: Path for combined PDF (default: output_dir/results_report.pdf)
        sample_days: If set, only plot first N days for readability (default: 14)

    Returns:
        List of created file paths.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    output_dir = Path(output_dir)
    images_dir = Path(images_dir) if images_dir else output_dir / "images"
    pdf_path = Path(pdf_path) if pdf_path else output_dir / "results_report.pdf"
    images_dir.mkdir(parents=True, exist_ok=True)

    consumption, loads, gen_p, soc, marginal_price, ts_full, pv = _load_results(output_dir)
    created: list[Path] = []

    if len(ts_full) == 0:
        raise ValueError("No time series data found")

    if sample_days:
        cutoff = ts_full[0] + pd.Timedelta(days=sample_days)
        mask = ts_full <= cutoff
        ts = ts_full[mask]
    else:
        ts = ts_full

    def _trim(df: pd.DataFrame) -> pd.DataFrame:
        if df is None:
            return None
        return df.loc[df.index.intersection(ts)]

    loads_t = _trim(loads)
    gen_p_t = _trim(gen_p)
    soc_t = _trim(soc)
    marginal_price_t = _trim(marginal_price)

    figs: list[tuple[plt.Figure, str]] = []

    # 1. Consumption / load profile
    fig, ax = plt.subplots(figsize=(12, 4))
    if loads_t is not None and "demand" in loads_t.columns:
        ax.plot(loads_t.index, loads_t["demand"] * 1000, label="Demand (kW)", color="C0")
    elif consumption is not None:
        c = consumption[consumption["timestamp"].isin(ts)]
        if not c.empty:
            ax.plot(c["timestamp"], c["power_kw"], label="Consumption (kW)", color="C0")
    ax.set_xlabel("Time")
    ax.set_ylabel("Power (kW)")
    ax.set_title("Electricity Consumption / Demand")
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    figs.append((fig, "consumption"))
    png_path = images_dir / "consumption.png"
    fig.savefig(png_path, dpi=150)
    created.append(png_path)
    plt.close(fig)

    # 2. Consumption and PV generation in one chart
    if (consumption is not None or (loads_t is not None and "demand" in loads_t.columns)) and pv is not None:
        fig, ax = plt.subplots(figsize=(12, 4))
        if loads_t is not None and "demand" in loads_t.columns:
            ax.plot(loads_t.index, loads_t["demand"] * 1000, label="Verbruik (kW)", color="C0")
        elif consumption is not None:
            c = consumption[consumption["timestamp"].isin(ts)]
            if not c.empty:
                ax.plot(c["timestamp"], c["power_kw"], label="Verbruik (kW)", color="C0")
        pv_trimmed = pv[pv["timestamp"].isin(ts)]
        if not pv_trimmed.empty:
            ax.plot(pv_trimmed["timestamp"], pv_trimmed["power_kw"], label="PV generatie (kW)", color="C1", alpha=0.9)
        ax.set_xlabel("Tijd")
        ax.set_ylabel("Vermogen (kW)")
        ax.set_title("Verbruik en PV-generatie")
        ax.legend(loc="upper right")
        ax.grid(True, alpha=0.3)
        fig.text(
            0.5, -0.02,
            "Let op: PV-data loopt mogelijk niet over dezelfde periode als verbruik; ontbrekende jaren zijn geprojecteerd (bijv. 2023→2025) — alleen voor simulatie.",
            ha="center", fontsize=8, color="gray",
        )
        fig.tight_layout(rect=[0, 0.06, 1, 1])
        png_path = images_dir / "consumption_and_pv.png"
        fig.savefig(png_path, dpi=150)
        created.append(png_path)
        plt.close(fig)

    # 2. Grid vs battery dispatch (if available)
    if gen_p_t is not None and loads_t is not None:
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.fill_between(loads_t.index, 0, loads_t["demand"] * 1000, alpha=0.5, label="Demand (kW)")
        if "grid" in gen_p_t.columns:
            ax.plot(gen_p_t.index, gen_p_t["grid"] * 1000, label="Grid supply (kW)", color="C1")
        ax.set_xlabel("Time")
        ax.set_ylabel("Power (kW)")
        ax.set_title("Grid Supply vs Demand")
        ax.legend(loc="upper right")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        figs.append((fig, "grid_dispatch"))
        png_path = images_dir / "grid_dispatch.png"
        fig.savefig(png_path, dpi=150)
        created.append(png_path)
        plt.close(fig)

    # 3. Battery state of charge
    if soc_t is not None:
        fig, ax = plt.subplots(figsize=(12, 4))
        for col in soc_t.columns:
            ax.plot(soc_t.index, soc_t[col] * 1000, label=f"{col} (kWh)")
        ax.set_xlabel("Time")
        ax.set_ylabel("State of charge (kWh)")
        ax.set_title("Battery State of Charge")
        ax.legend(loc="upper right")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        figs.append((fig, "battery_soc"))
        png_path = images_dir / "battery_state_of_charge.png"
        fig.savefig(png_path, dpi=150)
        created.append(png_path)
        plt.close(fig)

    # 4. Marginal price
    if marginal_price_t is not None:
        fig, ax = plt.subplots(figsize=(12, 4))
        for col in marginal_price_t.columns:
            ax.plot(marginal_price_t.index, marginal_price_t[col], label=col)
        ax.set_xlabel("Time")
        ax.set_ylabel("Price (€/MWh)")
        ax.set_title("Marginal Price (Time-of-Use)")
        ax.legend(loc="upper right")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        figs.append((fig, "marginal_price"))
        png_path = images_dir / "marginal_price.png"
        fig.savefig(png_path, dpi=150)
        created.append(png_path)
        plt.close(fig)

    # 5. Battery charge/discharge
    sp_d = output_dir / "pypsa_results" / "storage_units-p_store.csv"
    sp_p = output_dir / "pypsa_results" / "storage_units-p_dispatch.csv"
    _index_cols = ("Unnamed: 0", "snapshot")

    def _storage_data(csv_path: Path, ts: pd.DatetimeIndex) -> tuple[pd.DataFrame, list[str]]:
        """Read storage series CSV; return (df with numeric data, list of data column names)."""
        if not csv_path.exists():
            return pd.DataFrame(), []
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        data_cols = [c for c in df.columns if c not in _index_cols]
        if not data_cols:
            return pd.DataFrame(), []
        out = df[data_cols].astype(float, errors="ignore")
        if not isinstance(out.index, pd.DatetimeIndex) and len(ts) == len(out):
            out.index = ts[: len(out)]
        return out, data_cols

    if sp_d.exists() and sp_p.exists():
        store_df, cols_s = _storage_data(sp_d, ts_full)
        dispatch_df, cols_d = _storage_data(sp_p, ts_full)
        if cols_s and cols_d:
            store_t = store_df.loc[store_df.index.intersection(ts)]
            dispatch_t = dispatch_df.loc[dispatch_df.index.intersection(ts)]
            fig, ax = plt.subplots(figsize=(12, 4))
            for col in store_t.columns:
                ax.plot(store_t.index, store_t[col] * 1000, label=f"{col} charge (kW)", alpha=0.8)
            for col in dispatch_t.columns:
                ax.plot(dispatch_t.index, -dispatch_t[col].astype(float) * 1000, label=f"{col} discharge (kW)", alpha=0.8)
            ax.axhline(0, color="gray", linestyle="--", alpha=0.5)
            ax.set_xlabel("Time")
            ax.set_ylabel("Power (kW, positive=charging)")
            ax.set_title("Battery Charge/Discharge")
            ax.legend(loc="upper right")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            png_path = images_dir / "battery_charge_discharge.png"
            fig.savefig(png_path, dpi=150)
            created.append(png_path)
            plt.close(fig)

    # 6. Distribution cost tables (PNG)
    dist_cost_path = output_dir / "pypsa_results" / "ea_sim_distribution_costs.csv"
    dist_cost_df: Optional[pd.DataFrame] = None
    if dist_cost_path.exists():
        dist_cost_df = pd.read_csv(dist_cost_path)
        calculators = dist_cost_df["calculator"].unique() if "calculator" in dist_cost_df.columns else ["unknown"]

        # Comparison table (only when multiple calculators)
        if len(calculators) >= 2:
            fig_cmp = _render_calculator_comparison(dist_cost_df)
            if fig_cmp is not None:
                png_path = images_dir / "dist_costs_comparison.png"
                fig_cmp.savefig(png_path, dpi=150, bbox_inches="tight")
                created.append(png_path)
                plt.close(fig_cmp)

        for calc_name in calculators:
            fig_summary = _render_distribution_cost_summary(dist_cost_df, calc_name)
            png_path = images_dir / f"dist_costs_summary_{calc_name}.png"
            fig_summary.savefig(png_path, dpi=150, bbox_inches="tight")
            created.append(png_path)
            plt.close(fig_summary)

            fig_monthly = _render_distribution_cost_monthly(dist_cost_df, calc_name)
            png_path = images_dir / f"dist_costs_monthly_{calc_name}.png"
            fig_monthly.savefig(png_path, dpi=150, bbox_inches="tight")
            created.append(png_path)
            plt.close(fig_monthly)

    # Combined PDF
    consumption2, loads2, gen_p2, soc2, marginal_price2, ts_full2, pv2 = _load_results(output_dir)
    if sample_days:
        cutoff = ts_full2[0] + pd.Timedelta(days=sample_days)
        mask = ts_full2 <= cutoff
        ts2 = ts_full2[mask]
    else:
        ts2 = ts_full2

    def _trim2(df):
        if df is None:
            return None
        return df.loc[df.index.intersection(ts2)]

    with PdfPages(pdf_path) as pdf:
        # Consumption
        fig, ax = plt.subplots(figsize=(10, 4))
        if loads2 is not None and "demand" in loads2.columns:
            lt = _trim2(loads2)
            if lt is not None:
                ax.plot(lt.index, lt["demand"] * 1000, label="Demand (kW)", color="C0")
        elif consumption2 is not None:
            c = consumption2[consumption2["timestamp"].isin(ts2)]
            if not c.empty:
                ax.plot(c["timestamp"], c["power_kw"], label="Consumption (kW)", color="C0")
        ax.set_xlabel("Time")
        ax.set_ylabel("Power (kW)")
        ax.set_title("Electricity Consumption / Demand")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        pdf.savefig(fig)
        plt.close(fig)

        # Consumption and PV in one chart
        if (consumption2 is not None or (loads2 is not None and "demand" in loads2.columns)) and pv2 is not None:
            fig, ax = plt.subplots(figsize=(10, 4))
            if loads2 is not None and "demand" in loads2.columns:
                lt = _trim2(loads2)
                if lt is not None:
                    ax.plot(lt.index, lt["demand"] * 1000, label="Verbruik (kW)", color="C0")
            elif consumption2 is not None:
                c = consumption2[consumption2["timestamp"].isin(ts2)]
                if not c.empty:
                    ax.plot(c["timestamp"], c["power_kw"], label="Verbruik (kW)", color="C0")
            pv_trimmed = pv2[pv2["timestamp"].isin(ts2)]
            if not pv_trimmed.empty:
                ax.plot(pv_trimmed["timestamp"], pv_trimmed["power_kw"], label="PV generatie (kW)", color="C1", alpha=0.9)
            ax.set_xlabel("Tijd")
            ax.set_ylabel("Vermogen (kW)")
            ax.set_title("Verbruik en PV-generatie")
            ax.legend()
            ax.grid(True, alpha=0.3)
            fig.text(0.5, -0.02, "Let op: PV kan geprojecteerd zijn voor ontbrekende jaren (bijv. 2023→2025) — alleen voor simulatie.", ha="center", fontsize=8, color="gray")
            fig.tight_layout(rect=[0, 0.06, 1, 1])
            pdf.savefig(fig)
            plt.close(fig)

        # Grid dispatch
        if gen_p2 is not None and loads2 is not None:
            fig, ax = plt.subplots(figsize=(10, 4))
            lt = _trim2(loads2)
            gt = _trim2(gen_p2)
            if lt is not None:
                ax.fill_between(lt.index, 0, lt["demand"] * 1000, alpha=0.5, label="Demand (kW)")
            if gt is not None and "grid" in gt.columns:
                ax.plot(gt.index, gt["grid"] * 1000, label="Grid supply (kW)", color="C1")
            ax.set_xlabel("Time")
            ax.set_ylabel("Power (kW)")
            ax.set_title("Grid Supply vs Demand")
            ax.legend()
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

        # Battery SOC
        if soc2 is not None:
            fig, ax = plt.subplots(figsize=(10, 4))
            st = _trim2(soc2)
            if st is not None:
                for col in st.columns:
                    ax.plot(st.index, st[col] * 1000, label=f"{col} (kWh)")
            ax.set_xlabel("Time")
            ax.set_ylabel("State of charge (kWh)")
            ax.set_title("Battery State of Charge")
            ax.legend()
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

        # Marginal price
        if marginal_price2 is not None:
            fig, ax = plt.subplots(figsize=(10, 4))
            mt = _trim2(marginal_price2)
            if mt is not None:
                for col in mt.columns:
                    ax.plot(mt.index, mt[col], label=col)
            ax.set_xlabel("Time")
            ax.set_ylabel("Price (€/MWh)")
            ax.set_title("Marginal Price (Time-of-Use)")
            ax.legend()
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

        # Battery charge/discharge
        if sp_d.exists() and sp_p.exists():
            store_df2, cols_s2 = _storage_data(sp_d, ts_full2)
            dispatch_df2, cols_d2 = _storage_data(sp_p, ts_full2)
            if cols_s2 and cols_d2:
                store_t = _trim2(store_df2)
                dispatch_t = _trim2(dispatch_df2)
                if store_t is not None and dispatch_t is not None:
                    fig, ax = plt.subplots(figsize=(10, 4))
                    for col in store_t.columns:
                        ax.plot(store_t.index, store_t[col] * 1000, label=f"{col} charge (kW)")
                    for col in dispatch_t.columns:
                        ax.plot(dispatch_t.index, -dispatch_t[col].astype(float) * 1000, label=f"{col} discharge (kW)")
                    ax.axhline(0, color="gray", linestyle="--", alpha=0.5)
                    ax.set_xlabel("Time")
                    ax.set_ylabel("Power (kW)")
                    ax.set_title("Battery Charge/Discharge")
                    ax.legend()
                    ax.grid(True, alpha=0.3)
                    fig.tight_layout()
                    pdf.savefig(fig)
                    plt.close(fig)

        # Distribution cost tables
        if dist_cost_df is not None:
            calculators = dist_cost_df["calculator"].unique() if "calculator" in dist_cost_df.columns else ["unknown"]

            if len(calculators) >= 2:
                fig_cmp = _render_calculator_comparison(dist_cost_df)
                if fig_cmp is not None:
                    pdf.savefig(fig_cmp, bbox_inches="tight")
                    plt.close(fig_cmp)

            for calc_name in calculators:
                fig_summary = _render_distribution_cost_summary(dist_cost_df, calc_name)
                pdf.savefig(fig_summary, bbox_inches="tight")
                plt.close(fig_summary)

                fig_monthly = _render_distribution_cost_monthly(dist_cost_df, calc_name)
                pdf.savefig(fig_monthly, bbox_inches="tight")
                plt.close(fig_monthly)

    created.append(pdf_path)
    return created
