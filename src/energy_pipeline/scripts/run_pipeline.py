"""CLI for running the electricity consumption simulation pipeline."""

import argparse
import re
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from ..adapters.base import AdapterRegistry, ConsumptionAdapter, get_default_registry
from ..normalizer import aggregate_profiles, project_pv_to_consumption_dates, resample_profile
from ..schema import ConsumptionProfile
from ..simulation.battery_config import load_config, load_ea_sim_config, load_raw_config
from ..simulation.pypsa_builder import build_and_optimize


def _monthly_consumption_csv(profile: ConsumptionProfile) -> pd.DataFrame:
    """Aggregate consumption profile by month: energy_kwh and mean_power_kw per month."""
    df = profile.data.copy()
    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"])
        month = ts.dt.to_period("M")
    else:
        ts = pd.DatetimeIndex(df.index)
        month = ts.to_period("M")
    interval_hours = profile.interval_minutes / 60
    df = df.assign(month=month, energy_kwh=df["power_kw"] * interval_hours)
    grouped = df.groupby("month", as_index=False).agg(
        energy_kwh=("energy_kwh", "sum"), mean_power_kw=("power_kw", "mean")
    )
    monthly = pd.DataFrame(grouped)
    monthly["month"] = monthly["month"].astype(str)
    return monthly


# 18-digit EAN in filename (e.g. Smulders, Belgian DSO exports)
EAN_IN_PATH = re.compile(r"(\d{18})")


def _ean_from_path(path: Path) -> str | None:
    """Extract 18-digit EAN from path (e.g. filename). Returns None if not found."""
    m = EAN_IN_PATH.search(path.name)
    return m.group(1) if m else None


def _normalized_ean_id(profile: ConsumptionProfile, path: Path) -> str:
    """Return identifier for temp filenames: EAN if present, else safe path stem."""
    if profile.source_identifier and re.match(r"^\d{18}$", profile.source_identifier):
        return profile.source_identifier
    stem = path.stem
    safe = re.sub(r"[^\w\-.]", "_", stem).strip("_") or "normalized"
    return safe


def _discover_project_files(
    project_dir: Path, registry: AdapterRegistry
) -> list[tuple[Path, ConsumptionAdapter]]:
    """Find all files in project_dir that any registered adapter can handle. Returns [(path, adapter), ...]."""
    pairs: list[tuple[Path, ConsumptionAdapter]] = []
    for p in sorted(project_dir.iterdir()):
        if not p.is_file():
            continue
        adapter = registry.detect(p)
        if adapter is not None:
            pairs.append((p, adapter))
    return pairs


def _export_pypsa_optimization_series(n, path: Path) -> None:
    """Write optimization time series to CSV so visualizations can load them."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    # Generator dispatch (grid supply)
    if hasattr(n, "generators_t") and "p" in n.generators_t:
        n.generators_t["p"].to_csv(path / "generators-p.csv")
    # Storage: state of charge, p_store, p_dispatch
    if hasattr(n, "storage_units_t"):
        su_t = n.storage_units_t
        if "state_of_charge" in su_t:
            su_t["state_of_charge"].to_csv(path / "storage_units-state_of_charge.csv")
        if "p_store" in su_t:
            su_t["p_store"].to_csv(path / "storage_units-p_store.csv")
        if "p_dispatch" in su_t:
            su_t["p_dispatch"].to_csv(path / "storage_units-p_dispatch.csv")
    # Bus marginal price (time-of-use shadow price)
    if hasattr(n, "buses_t") and "marginal_price" in n.buses_t:
        n.buses_t["marginal_price"].to_csv(path / "buses-marginal_price.csv")


_LINE_WIDTH = 70


class _SectionInfo:
    """Accumulates summary info within a pipeline section."""

    def __init__(self) -> None:
        self.summary = ""


@contextmanager
def _section(title: str):
    """Print section header/footer with elapsed time."""
    header = f"══ {title} " + "═" * max(0, _LINE_WIDTH - len(title) - 4)
    print(f"\n{header}")
    t0 = time.perf_counter()
    info = _SectionInfo()
    try:
        yield info
    finally:
        elapsed = time.perf_counter() - t0
        tag = info.summary or "done"
        left = f"── {tag} "
        right = f" {elapsed:.2f}s ──"
        pad = max(0, _LINE_WIDTH - len(left) - len(right))
        print(f"{left}{'─' * pad}{right}")


def _pipeline_done(t0: float) -> None:
    elapsed = time.perf_counter() - t0
    print(f"\n{'═' * _LINE_WIDTH}")
    print(f"  Pipeline completed in {elapsed:.2f}s")
    print(f"{'═' * _LINE_WIDTH}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert electricity consumption to normalized format and run PyPSA battery optimization"
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to consumption file or project folder (e.g. data/projects/smulders); folder = all files aggregated",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("./output"),
        help="Output directory for normalized profile and results",
    )
    parser.add_argument(
        "--adapter",
        "-a",
        type=str,
        default=None,
        help="Force adapter by name (e.g. belgian_dso). If not set, auto-detect.",
    )
    parser.add_argument(
        "--resample",
        "-r",
        type=str,
        default="15min",
        help="Resample rule for normalization (15min, 1h, etc.)",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to config YAML. Uses default if not specified.",
    )
    parser.add_argument(
        "--no-simulate",
        action="store_true",
        help="Skip PyPSA simulation, only convert to normalized format",
    )
    parser.add_argument(
        "--visualize",
        "--viz",
        action="store_true",
        help="Generate PDF and PNG visualizations of results",
    )
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=None,
        help="Directory for PNG images (default: output-dir/images)",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help="Path for combined PDF report (default: output-dir/results_report.pdf)",
    )
    parser.add_argument(
        "--viz-days",
        type=int,
        default=14,
        help="Number of days to plot for readability (default: 14, 0=all)",
    )
    parser.add_argument(
        "--simulator",
        "-s",
        type=str,
        default=None,
        choices=["pypsa", "ea_sim"],
        help="Simulation engine: pypsa (optimization) or ea_sim (rule-based). Default: from config.",
    )
    parser.add_argument(
        "--calculator",
        type=str,
        nargs="+",
        default=None,
        metavar="NAME",
        help="Distribution cost calculator(s) for EA Sim: belgian_general, static_qv. Default: all.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose solver output",
    )
    parser.add_argument(
        "--save-monthly",
        action="store_true",
        help="Also save consumption profile aggregated per month to consumption_profile_monthly.csv",
    )
    parser.add_argument(
        "--save-normalized",
        action="store_true",
        help="Also save per-EAN normalized profiles to output-dir/temp/ (folder input only)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Ignore temp cache: re-parse all inputs and overwrite temp/normalization files (remove and recreate temp/)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Limit analysis to a specific calendar year (YYYY).",
    )

    def _parse_months(value: str | None) -> list[int] | None:
        if value is None:
            return None
        parts = [p.strip() for p in value.split(",") if p.strip()]
        months: list[int] = []
        for p in parts:
            try:
                m = int(p)
            except ValueError:
                raise argparse.ArgumentTypeError(f"Invalid month value: {p!r}")
            if not 1 <= m <= 12:
                raise argparse.ArgumentTypeError(f"Month out of range (1-12): {m}")
            months.append(m)
        return months

    parser.add_argument(
        "--month",
        type=_parse_months,
        default=None,
        metavar="M[,M...]",
        help="Optional: month (1-12) or comma-separated list of months (e.g. 1,2,3). Requires --year.",
    )
    args = parser.parse_args()
    if args.month is not None and args.year is None:
        print("Error: --month can only be used together with --year.", file=sys.stderr)
        return 1
    pipeline_t0 = time.perf_counter()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file or folder not found: {input_path}", file=sys.stderr)
        return 1

    out_dir = Path(args.output_dir)
    temp_dir = out_dir / "temp"

    # ── CONFIGURATION ─────────────────────────────────────────
    with _section("CONFIGURATION") as sec:
        registry = get_default_registry()
        config_path = args.config
        if config_path is None:
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            config_path = project_root / "config" / "default.yaml"
        config = load_config(config_path)
        raw_config = load_raw_config(config_path)
        simulator = args.simulator or raw_config.get("simulator", "pypsa")
        print(f"Config:    {config_path}")
        print(f"Simulator: {simulator}")
        print(f"Output:    {out_dir}")
        sec.summary = "loaded"

    # ── DATA LOADING ──────────────────────────────────────────
    with _section("DATA LOADING") as sec:
        profile: ConsumptionProfile
        per_file_profiles: list[tuple[Path, ConsumptionProfile]] | None = None

        if input_path.is_dir():
            pairs = _discover_project_files(input_path, registry)
            if not pairs:
                print(f"Error: no supported consumption files found in {input_path}", file=sys.stderr)
                sec.summary = "error — no files found"
                return 1
            print(f"Project folder: {input_path.name} — {len(pairs)} file(s)")
            profiles = []
            for path, adapter in pairs:
                ean = _ean_from_path(path)
                cache_quarterly = temp_dir / f"ean_quarterly_{ean}.csv" if ean else None
                if (
                    not args.rebuild
                    and cache_quarterly
                    and cache_quarterly.exists()
                ):
                    prof = ConsumptionProfile.from_csv(
                        cache_quarterly,
                        source_identifier=ean or "",
                        interval_minutes=15,
                    )
                    print(f"  {path.name} (cached, EAN: {ean}) — {len(prof.data):,} datapoints")
                else:
                    raw = adapter.parse(path)
                    if hasattr(raw, "attrs"):
                        raw.attrs["path"] = path
                    prof = adapter.to_normalized(raw)
                    print(
                        f"  {path.name} (adapter: {adapter.name},"
                        f" EAN: {prof.source_identifier or '-'}) — {len(prof.data):,} datapoints"
                    )
                profiles.append(prof)
            per_file_profiles = [(path, prof) for (path, _), prof in zip(pairs, profiles)]
            profile = aggregate_profiles(profiles, source_identifier=input_path.name)
        else:
            adapter: ConsumptionAdapter | None = None
            if args.adapter:
                adapter = registry.get(args.adapter)
                if not adapter:
                    print(f"Error: unknown adapter '{args.adapter}'", file=sys.stderr)
                    print(f"Available: {registry.list_names()}", file=sys.stderr)
                    sec.summary = "error — unknown adapter"
                    return 1
            else:
                adapter = registry.detect(input_path)
                if not adapter:
                    print("Error: could not detect file format. Use --adapter to specify.", file=sys.stderr)
                    sec.summary = "error — format not detected"
                    return 1
            print(f"Adapter: {adapter.name}")
            raw = adapter.parse(input_path)
            if hasattr(raw, "attrs"):
                raw.attrs["path"] = input_path
            profile = adapter.to_normalized(raw)  # type: ignore[reportAssignmentType]
            per_file_profiles = None

        n_points = len(profile.data)
        ts = profile.timestamps
        pw = profile.power_kw

        if args.year is not None:
            mask = ts.year == args.year  # type: ignore[reportAttributeAccessIssue]
            if args.month is not None:
                mask = mask & ts.month.isin(args.month)  # type: ignore[reportAttributeAccessIssue]

            if not mask.any():
                msg = f"Error: no datapoints for year={args.year}"
                if args.month is not None:
                    msg += f" and month(s)={args.month}"
                print(msg, file=sys.stderr)
                sec.summary = "error — no datapoints for requested period"
                return 1

            # Apply time filter to profile
            filtered_series = profile.power_kw[mask]
            profile = ConsumptionProfile.from_series(
                pd.Series(filtered_series),
                source_identifier=profile.source_identifier,
                interval_minutes=profile.interval_minutes,
            )
            ts = profile.timestamps
            pw = profile.power_kw

        # Help the type checker: timestamps is always a DatetimeIndex here
        ts_min = pd.Timestamp(ts.min())  # type: ignore[reportUnknownMemberType]
        ts_max = pd.Timestamp(ts.max())  # type: ignore[reportUnknownMemberType]
        delta = ts_max - ts_min
        days = int(delta.days)
        print(f"Datapoints: {n_points:,} | Interval: {profile.interval_minutes} min")
        print(f"Range:      {ts.min():%Y-%m-%d %H:%M} → {ts.max():%Y-%m-%d %H:%M} ({days} days)")
        print(f"Peak power: {pw.max():,.1f} kW | Mean: {pw.mean():,.1f} kW")
        sec.summary = f"{n_points:,} datapoints loaded"

    # ── NORMALIZATION & EXPORT ────────────────────────────────
    with _section("NORMALIZATION & EXPORT") as sec:
        if args.resample and args.resample not in ("15min", "15T"):
            rule = args.resample.replace("H", "h").replace("D", "d")
            n_before = len(profile.data)
            profile = resample_profile(profile, rule=rule)
            print(f"Resampled {rule}: {n_before:,} → {len(profile.data):,} datapoints")

        out_dir.mkdir(parents=True, exist_ok=True)
        files_exported = 0

        norm_path = out_dir / "consumption_profile.csv"
        profile.data.to_csv(norm_path, index=False)
        print(f"Saved {norm_path.name} ({len(profile.data):,} rows)")
        files_exported += 1

        consumption_hourly = resample_profile(profile, rule="1h", aggregation="mean")
        consumption_hourly_path = out_dir / "consumption_profile_hourly.csv"
        consumption_hourly.data.to_csv(consumption_hourly_path, index=False)
        print(f"Saved {consumption_hourly_path.name} ({len(consumption_hourly.data):,} rows)")
        files_exported += 1

        if args.save_monthly:
            monthly_path = out_dir / "consumption_profile_monthly.csv"
            monthly_df = _monthly_consumption_csv(profile)
            monthly_df.to_csv(monthly_path, index=False)
            print(f"Saved {monthly_path.name} ({len(monthly_df):,} rows)")
            files_exported += 1

        sec.summary = f"{files_exported} file(s) exported"

    # ── PV DATA ───────────────────────────────────────────────
    pv_profile: ConsumptionProfile | None = None
    pv_dir = None
    if input_path.is_dir():
        if (input_path / "pv").is_dir():
            pv_dir = input_path / "pv"
        elif (out_dir / "pv").is_dir():
            pv_dir = out_dir / "pv"
    if pv_dir is not None:
        pairs_pv = _discover_project_files(pv_dir, registry)
        if pairs_pv:
            with _section("PV DATA") as sec:
                print(f"PV folder: {pv_dir} — {len(pairs_pv)} file(s)")
                pv_profiles = []
                for path, adapter in pairs_pv:
                    raw = adapter.parse(path)
                    if hasattr(raw, "attrs"):
                        raw.attrs["path"] = path
                    pv_prof = adapter.to_normalized(raw)
                    print(f"  {path.name} (adapter: {adapter.name}) — {len(pv_prof.data):,} datapoints")
                    pv_profiles.append(pv_prof)
                pv_profile = aggregate_profiles(pv_profiles, source_identifier="pv_aggregated")
                if args.resample and args.resample not in ("15min", "15T"):
                    rule = args.resample.replace("H", "h").replace("D", "d")
                    pv_profile = resample_profile(pv_profile, rule=rule)
                pv_profile, pv_was_projected = project_pv_to_consumption_dates(pv_profile, profile)
                if pv_was_projected:
                    print("PV projected from last available year to match consumption period")
                pv_files = 0
                pv_norm_path = out_dir / "pv_generation_profile.csv"
                pv_profile.data.to_csv(pv_norm_path, index=False)
                print(f"Saved {pv_norm_path.name} ({len(pv_profile.data):,} rows)")
                pv_files += 1
                pv_hourly = resample_profile(pv_profile, rule="1h", aggregation="mean")
                pv_hourly_path = out_dir / "pv_generation_profile_hourly.csv"
                pv_hourly.data.to_csv(pv_hourly_path, index=False)
                print(f"Saved {pv_hourly_path.name} ({len(pv_hourly.data):,} rows)")
                pv_files += 1
                if args.save_monthly:
                    pv_monthly_path = out_dir / "pv_generation_profile_monthly.csv"
                    pv_monthly_df = _monthly_consumption_csv(pv_profile)
                    pv_monthly_df.to_csv(pv_monthly_path, index=False)
                    print(f"Saved {pv_monthly_path.name} ({len(pv_monthly_df):,} rows)")
                    pv_files += 1
                pv_ts = pv_profile.timestamps
                pv_pw = pv_profile.power_kw
                print(f"PV range: {pv_ts.min():%Y-%m-%d} → {pv_ts.max():%Y-%m-%d} | Peak: {pv_pw.max():,.1f} kW")
                sec.summary = f"{len(pv_profile.data):,} PV datapoints | {pv_files} file(s) exported"
        else:
            print(f"\nPV folder present but no supported files found in {pv_dir}")

    # ── PER-EAN EXPORT ────────────────────────────────────────
    if args.save_normalized and per_file_profiles:
        with _section("PER-EAN EXPORT") as sec:
            if args.rebuild and temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)
            for path, prof in per_file_profiles:
                ean_id = _normalized_ean_id(prof, path)
                prof.data.to_csv(temp_dir / f"ean_quarterly_{ean_id}.csv", index=False)
                hourly = resample_profile(prof, rule="1h", aggregation="mean")
                hourly.data.to_csv(temp_dir / f"ean_hourly_{ean_id}.csv", index=False)
            n_profiles = len(per_file_profiles)
            print(f"Saved {n_profiles} profile(s) (quarterly + hourly) to {temp_dir}")
            sec.summary = f"{n_profiles * 2} file(s) exported"

    # ── EARLY EXIT (--no-simulate) ────────────────────────────
    if args.no_simulate:
        if args.visualize:
            with _section("VISUALIZATION") as sec:
                try:
                    from ..visualization.plots import visualize_results
                    sample_days = args.viz_days if args.viz_days > 0 else None
                    paths = visualize_results(
                        out_dir,
                        images_dir=args.images_dir,
                        pdf_path=args.pdf,
                        sample_days=sample_days,
                    )
                    for p in paths:
                        print(f"  {p}")
                    sec.summary = f"{len(paths)} file(s) generated"
                except Exception as e:
                    print(f"Warning: visualization failed: {e}", file=sys.stderr)
                    sec.summary = "failed"
        _pipeline_done(pipeline_t0)
        return 0

    # ── SIMULATION ────────────────────────────────────────────
    sim_dir = out_dir / "pypsa_results"
    ea_result = None

    with _section(f"SIMULATION — {simulator.upper()}") as sec:
        n_timesteps = len(profile.data)

        if simulator == "ea_sim":
            from ..simulation.ea_sim import simulate as ea_simulate

            ea_config = load_ea_sim_config(config_path)
            print(f"Strategy:  {ea_config.strategy}")
            print(
                f"Battery:   {ea_config.battery_capacity_kwh:,.0f} kWh /"
                f" {ea_config.battery_power_kw:,.0f} kW"
            )
            print(f"Timesteps: {n_timesteps:,} | Interval: {profile.interval_minutes} min")
            result, status = ea_simulate(
                profile,
                ea_config,
                pv_profile=pv_profile,
                log_to_console=args.verbose,
            )
            print(f"Status:    {status}")
            ea_result = result

            try:
                result.export_to_csv_folder(sim_dir)
                print(f"Results exported to {sim_dir}")
            except Exception as e:
                print(f"Warning: could not export EA Sim: {e}", file=sys.stderr)

            sec.summary = f"{n_timesteps:,} timesteps simulated — {status}"
        else:
            print(f"Timesteps: {n_timesteps:,} | Interval: {profile.interval_minutes} min")
            n, status = build_and_optimize(
                profile, config=config, log_to_console=args.verbose, pv_profile=pv_profile
            )
            if isinstance(status, tuple):
                status_str = f"{status[0]} ({status[1]})"
            else:
                status_str = str(status)
            print(f"Status:    {status_str}")

            try:
                n.export_to_csv_folder(sim_dir)
                print(f"Results exported to {sim_dir}")
            except Exception as e:
                print(f"Warning: could not export PyPSA: {e}", file=sys.stderr)

            try:
                _export_pypsa_optimization_series(n, sim_dir)
            except Exception as e:
                print(f"Warning: could not export PyPSA series: {e}", file=sys.stderr)

            if hasattr(n, "storage_units") and "battery" in n.storage_units.index:
                p_nom = n.storage_units.loc["battery", "p_nom_opt"]
                if hasattr(p_nom, "item"):
                    p_nom = float(p_nom)
                print(f"Optimal battery power: {p_nom:.2f} MW")

            sec.summary = f"{n_timesteps:,} timesteps optimized — {status_str}"

    # ── CALCULATION MODEL (pricing) ────────────────────────────
    if simulator == "ea_sim" and ea_result is not None:
        ea_config = load_ea_sim_config(config_path)
        if ea_config.dist_cost_enabled:
            from ..simulation.ea_sim import compute_distribution_scenario_costs

            with _section("CALCULATION MODEL — DISTRIBUTION COSTS") as sec:
                ea_result.scenario_costs = compute_distribution_scenario_costs(
                    ea_result,
                    ea_config,
                    calculator_names=args.calculator,
                    log_to_console=args.verbose,
                )
                try:
                    # Re-export so ea_sim_distribution_costs.csv is written
                    ea_result.export_to_csv_folder(sim_dir)
                except Exception as e:
                    print(f"Warning: could not export EA Sim costs: {e}", file=sys.stderr)
                sec.summary = "computed & exported"

    # ── VISUALIZATION ─────────────────────────────────────────
    if args.visualize:
        with _section("VISUALIZATION") as sec:
            try:
                from ..visualization.plots import visualize_results
                sample_days = args.viz_days if args.viz_days > 0 else None
                paths = visualize_results(
                    out_dir,
                    images_dir=args.images_dir,
                    pdf_path=args.pdf,
                    sample_days=sample_days,
                )
                for p in paths:
                    print(f"  {p}")
                sec.summary = f"{len(paths)} file(s) generated"
            except Exception as e:
                print(f"Warning: visualization failed: {e}", file=sys.stderr)
                sec.summary = "failed"

    _pipeline_done(pipeline_t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
