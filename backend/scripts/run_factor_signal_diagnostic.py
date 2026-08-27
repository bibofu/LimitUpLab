"""Run the independent factor-signal falsification diagnostic from the CLI.

Usage:
    ./.venv/Scripts/python.exe scripts/run_factor_signal_diagnostic.py
    ./.venv/Scripts/python.exe scripts/run_factor_signal_diagnostic.py \\
        --outcome-measure three_day_open_to_close_pct

The script re-rates every local first-board candidate that already has a
next-day outcome and asks, honestly, whether any of the 14 scoring factors
carries usable cross-sectional signal yet. It prints a human-readable summary
and writes the full report to backend/data/factor_signal_diagnostic_latest.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.repositories import SQLiteFirstBoardRepository, SQLiteLimitUpRepository
from app.services.factor_signal_diagnostic import build_factor_signal_diagnostic


def _configure_utf8_stdout() -> None:
    """Print Chinese legibly under a non-UTF-8 Windows console."""

    encoding = getattr(sys.stdout, "encoding", "") or ""
    if encoding.lower() not in {"utf-8", "utf8"}:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main() -> None:
    """Run the factor-signal diagnostic and print/save the report."""

    _configure_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Falsify the 14 first-board scoring factors against next-day outcomes.",
    )
    parser.add_argument(
        "--start-date",
        type=_date_arg,
        default=None,
        help="Inclusive start trade date (YYYY-MM-DD). Defaults to the earliest local date.",
    )
    parser.add_argument(
        "--end-date",
        type=_date_arg,
        default=None,
        help="Inclusive end trade date (YYYY-MM-DD). Defaults to the latest local date.",
    )
    parser.add_argument(
        "--outcome-measure",
        default="next_open_to_close_pct",
        choices=["next_open_to_close_pct", "three_day_open_to_close_pct"],
        help="Post-board outcome to test signal against (default: next_open_to_close_pct).",
    )
    parser.add_argument(
        "--json-output",
        default=str(BACKEND_ROOT / "data" / "factor_signal_diagnostic_latest.json"),
        help="Where to write the full JSON report.",
    )
    args = parser.parse_args()

    limit_repo = SQLiteLimitUpRepository(seed_if_empty=False)
    first_board_repo = SQLiteFirstBoardRepository()
    events = limit_repo.list_events()
    if not events:
        print("No local limit-up events available; run the daily pipeline first.")
        raise SystemExit(1)

    available_dates = sorted({event.trade_date for event in events})
    start_date = args.start_date or available_dates[0]
    end_date = args.end_date or available_dates[-1]
    if start_date > end_date:
        print(f"start_date {start_date} is after end_date {end_date}.")
        raise SystemExit(2)

    response = build_factor_signal_diagnostic(
        events=events,
        start_date=start_date,
        end_date=end_date,
        first_board_repository=first_board_repo,
        outcome_measure=args.outcome_measure,
    )

    output_path = Path(args.json_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _print_summary(response, output_path)


def _date_arg(value: str) -> date:
    """Parse a YYYY-MM-DD argument into a date."""

    return date.fromisoformat(value)


def _print_summary(response, output_path: Path) -> None:
    """Print a compact human-readable summary of the diagnostic."""

    print(
        f"\nFactor signal diagnostic  n={response.sample_size}  "
        f"trade_dates={response.trade_date_count}  "
        f"outcome={response.outcome_measure}  "
        f"scoring={response.scoring_version}"
    )
    print(f"  range: {response.start_date} .. {response.end_date}")
    print(f"  bonferroni alpha = {response.bonferroni_alpha}")
    print()
    print(
        f"  {'factor':<22} {'n':>4} {'rho':>7} {'p':>8} {'sig':>3} "
        f"{'spread':>8} {'dir':>11}"
    )
    print("  " + "-" * 70)
    for row in response.factors:
        rho = "  N/A" if row.spearman_rho is None else f"{row.spearman_rho:+.3f}"
        p = "  N/A" if row.p_value is None else f"{row.p_value:.4f}"
        spread = (
            "  N/A"
            if row.tercile_spread_pct is None
            else f"{row.tercile_spread_pct:+.2f}"
        )
        print(
            f"  {row.factor_name:<20} {row.sample_size:>4} {rho:>7} {p:>8} "
            f"{'yes' if row.significant_after_bonferroni else '':>3} {spread:>8} "
            f"{row.direction:>11}"
        )
    lasso = response.lasso
    print()
    print(
        f"  Lasso: retained {lasso.retained_factor_count}/14 at alpha={lasso.lasso_alpha:.4g}"
        f" (alpha_max={lasso.alpha_max:.4g})"
    )
    print(
        f"  OLS R2={lasso.ols_r2}  adjusted R2={lasso.ols_adjusted_r2}"
        f"  bootstrap_max_retention={lasso.bootstrap_max_retention_rate}"
    )
    if lasso.retained_factor_keys:
        print(f"  retained: {', '.join(lasso.retained_factor_keys)}")
    print()
    print(f"  strongest factor: {response.strongest_factor_key}")
    print()
    print("VERDICT")
    print("  " + response.verdict)
    print()
    if response.warnings:
        print("WARNINGS")
        for warning in response.warnings:
            print(f"  - {warning}")
        print()
    print("CAVEATS")
    for caveat in response.caveats:
        print(f"  - {caveat}")
    print()
    print(f"Full report written to: {output_path}")


if __name__ == "__main__":
    main()
