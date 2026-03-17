"""Standalone CLI to visualize existing pipeline output."""

import argparse
import sys
from pathlib import Path

from ..visualization.plots import visualize_results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate PDF and PNG visualizations from pipeline output"
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        nargs="?",
        default=Path("./output"),
        help="Output directory containing consumption_profile.csv and/or pypsa_results/",
    )
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=None,
        help="Directory for PNG images (default: output_dir/images)",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help="Path for combined PDF (default: output_dir/results_report.pdf)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Number of days to plot (default: 14, 0=all)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not output_dir.exists():
        print(f"Error: directory not found: {output_dir}", file=sys.stderr)
        return 1

    try:
        sample_days = args.days if args.days > 0 else None
        paths = visualize_results(
            output_dir,
            images_dir=args.images_dir,
            pdf_path=args.pdf,
            sample_days=sample_days,
        )
        print(f"Saved {len(paths)} visualization(s):")
        for p in paths:
            print(f"  {p}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
