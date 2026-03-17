"""Run EA Sim battery simulation on existing normalized consumption data."""

import argparse
import sys
from pathlib import Path

from ..schema import ConsumptionProfile
from ..simulation.battery_config import load_ea_sim_config
from ..simulation.distribution_costs import available_calculators
from ..simulation.ea_sim import simulate, compute_distribution_scenario_costs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run EnergyLabs rule-based BESS simulation on existing normalized profiles"
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Output directory containing consumption_profile.csv (and optionally pv_generation_profile.csv)",
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=None,
        help="Path to config YAML. Uses config/default.yaml if not specified.",
    )
    parser.add_argument(
        "--calculator",
        type=str,
        nargs="+",
        default=None,
        metavar="NAME",
        help=(
            "Distribution cost calculator(s) to run. "
            f"Available: {', '.join(available_calculators())}. "
            "Default: all calculators."
        ),
    )
    parser.add_argument(
        "--visualize", "--viz",
        action="store_true",
        help="Generate PDF and PNG visualizations after simulation",
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
        "--verbose", "-v",
        action="store_true",
        help="Verbose simulation output",
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
        out_dir = input_path.parent
    else:
        out_dir = input_path

    profile_path = out_dir / "consumption_profile.csv"
    if not profile_path.exists():
        print(f"Error: consumption_profile.csv not found in {out_dir}", file=sys.stderr)
        return 1

    config_path = args.config
    if config_path is None:
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        config_path = project_root / "config" / "default.yaml"
    ea_config = load_ea_sim_config(config_path)

    profile = ConsumptionProfile.from_csv(profile_path)

    pv_profile = None
    pv_path = out_dir / "pv_generation_profile.csv"
    if pv_path.exists():
        pv_profile = ConsumptionProfile.from_csv(pv_path, source_identifier="pv")
        print(f"Loaded PV profile: {len(pv_profile.data)} timestamps")

    calc_names = args.calculator
    if calc_names:
        all_calcs = available_calculators()
        for c in calc_names:
            if c not in all_calcs:
                print(f"Error: unknown calculator {c!r}. Available: {', '.join(all_calcs)}", file=sys.stderr)
                return 1

    print(f"Running EA Sim ({ea_config.strategy}) …")
    result, status = simulate(
        profile,
        ea_config,
        pv_profile=pv_profile,
        log_to_console=args.verbose,
    )
    print(f"Simulation: {status}")

    # Distribution-cost calculation as a dedicated post-processing step
    if ea_config.dist_cost_enabled:
        title = "CALCULATION MODEL — DISTRIBUTION COSTS"
        line = "═" * 70
        print(f"\n══ {title} " + "═" * max(0, 70 - len(title) - 4))
        result.scenario_costs = compute_distribution_scenario_costs(
            result,
            ea_config,
            calculator_names=calc_names,
            log_to_console=args.verbose,
        )
        print(f"── done ─{line[:58]}")

    sim_dir = out_dir / "pypsa_results"
    try:
        result.export_to_csv_folder(sim_dir)
        print(f"Saved EA Sim results to {sim_dir}")
    except Exception as e:
        print(f"Warning: could not export EA Sim: {e}", file=sys.stderr)

    print(
        f"Battery: {result.battery_capacity_kwh:.0f} kWh / "
        f"{result.battery_p_nom_kw:.0f} kW"
    )

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
