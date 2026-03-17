"""Run PyPSA battery optimization on existing normalized consumption data."""

import argparse
import sys
from pathlib import Path

from ..schema import ConsumptionProfile
from ..simulation.battery_config import load_config
from ..simulation.pypsa_builder import build_and_optimize


def _export_pypsa_optimization_series(n, path: Path) -> None:
    """Write optimization time series to CSV so visualizations can load them."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if hasattr(n, "generators_t") and "p" in n.generators_t:
        n.generators_t["p"].to_csv(path / "generators-p.csv")
    if hasattr(n, "storage_units_t"):
        su_t = n.storage_units_t
        if "state_of_charge" in su_t:
            su_t["state_of_charge"].to_csv(path / "storage_units-state_of_charge.csv")
        if "p_store" in su_t:
            su_t["p_store"].to_csv(path / "storage_units-p_store.csv")
        if "p_dispatch" in su_t:
            su_t["p_dispatch"].to_csv(path / "storage_units-p_dispatch.csv")
    if hasattr(n, "buses_t") and "marginal_price" in n.buses_t:
        n.buses_t["marginal_price"].to_csv(path / "buses-marginal_price.csv")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run PyPSA battery optimization on existing normalized consumption data (consumption_profile.csv)"
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Output directory containing consumption_profile.csv, or path to consumption_profile.csv",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to config YAML. Uses config/default.yaml if not specified.",
    )
    parser.add_argument(
        "--visualize",
        "--viz",
        action="store_true",
        help="Generate PDF and PNG visualizations after optimization",
    )
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=None,
        help="Directory for PNG images (default: input_dir/images)",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help="Path for combined PDF report (default: input_dir/results_report.pdf)",
    )
    parser.add_argument(
        "--viz-days",
        type=int,
        default=14,
        help="Number of days to plot (default: 14, 0=all)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose solver output",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: path not found: {input_path}", file=sys.stderr)
        return 1

    if input_path.is_file():
        if input_path.name != "consumption_profile.csv":
            print("Error: when passing a file, it must be consumption_profile.csv", file=sys.stderr)
            return 1
        profile_path = input_path
        out_dir = input_path.parent
    else:
        profile_path = input_path / "consumption_profile.csv"
        out_dir = input_path
        if not profile_path.exists():
            print(f"Error: consumption_profile.csv not found in {out_dir}", file=sys.stderr)
            return 1

    config_path = args.config
    if config_path is None:
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        config_path = project_root / "config" / "default.yaml"
    config = load_config(config_path)

    profile = ConsumptionProfile.from_csv(profile_path)
    print("Running PyPSA battery optimization...")
    n, status = build_and_optimize(profile, config=config, log_to_console=args.verbose)
    if isinstance(status, tuple):
        status_str = f"{status[0]} ({status[1]})"
    else:
        status_str = str(status)
    print(f"Optimization: {status_str}")

    pypsa_dir = out_dir / "pypsa_results"
    try:
        n.export_to_csv_folder(pypsa_dir)
        print(f"Saved PyPSA results to {pypsa_dir}")
    except Exception as e:
        print(f"Warning: could not export PyPSA: {e}", file=sys.stderr)
    try:
        _export_pypsa_optimization_series(n, pypsa_dir)
    except Exception as e:
        print(f"Warning: could not export PyPSA series: {e}", file=sys.stderr)

    if hasattr(n, "storage_units") and "battery" in n.storage_units.index:
        p_nom = n.storage_units.loc["battery", "p_nom_opt"]
        if hasattr(p_nom, "item"):
            p_nom = float(p_nom)
        print(f"Optimal battery power: {p_nom:.2f} MW")

    if args.visualize:
        try:
            from ..visualization.plots import visualize_results
            sample_days = args.viz_days if args.viz_days > 0 else None
            paths = visualize_results(
                out_dir,
                images_dir=args.images_dir,
                pdf_path=args.pdf,
                sample_days=sample_days,
            )
            print(f"Saved visualizations: {len(paths)} file(s)")
            for p in paths:
                print(f"  - {p}")
        except Exception as e:
            print(f"Warning: visualization failed: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
