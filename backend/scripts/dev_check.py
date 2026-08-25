"""Local startup health check and data freshness gate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import hydrate_windows_environment
from app.repositories import SQLiteFirstBoardRepository, SQLiteLimitUpRepository
from app.services.system_health import build_agent_system_health
from scripts.update_daily_data import run_daily_update


def main() -> None:
    """Run development checks and optionally refresh data before startup."""

    parser = argparse.ArgumentParser(description="Check local LimitUpLab runtime health.")
    parser.add_argument(
        "--ensure-data",
        action="store_true",
        help="Try to fetch expected after-close data when local data is stale.",
    )
    parser.add_argument(
        "--skip-eval",
        action="store_true",
        help="Skip offline Agent eval for faster startup.",
    )
    parser.add_argument(
        "--json-output",
        default=str(BACKEND_ROOT / "data" / "dev_check_report.json"),
        help="Where to write the local health report.",
    )
    args = parser.parse_args()

    _apply_user_api_key()
    limit_repo = SQLiteLimitUpRepository(seed_if_empty=False)
    first_board_repo = SQLiteFirstBoardRepository()
    events = limit_repo.list_events()
    before = build_agent_system_health(
        events=events,
        first_board_repository=first_board_repo,
        run_offline_eval=not args.skip_eval,
    )

    update_report = None
    if args.ensure_data and before.expected_data_date:
        should_import = before.data_update_recommended
        update_date = (
            before.expected_data_date
            if should_import
            else before.latest_local_trade_date
        )
        try:
            if update_date:
                update_report = run_daily_update(
                    trade_date=update_date,
                    skip_import=not should_import,
                    replace_date=should_import,
                    max_tracked_kline_fetches=60,
                    limit_up_repository=limit_repo,
                    first_board_repository=first_board_repo,
                )
        except Exception as error:  # noqa: BLE001
            update_report = {
                "trade_date": update_date.isoformat() if update_date else None,
                "error": str(error),
            }

    after = build_agent_system_health(
        events=limit_repo.list_events(),
        first_board_repository=first_board_repo,
        run_offline_eval=not args.skip_eval,
    )
    report = {
        "before": before.model_dump(mode="json"),
        "after": after.model_dump(mode="json"),
        "update_report": (
            asdict(update_report)
            if hasattr(update_report, "__dataclass_fields__")
            else update_report
        ),
    }

    output_path = Path(args.json_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(_summary(report), ensure_ascii=False, indent=2))

    if after.status == "missing":
        raise SystemExit(1)


def _apply_user_api_key() -> None:
    """Mirror user/machine API keys into process env for local checks."""

    hydrate_windows_environment(("DEEPSEEK_API_KEY", "OPENAI_API_KEY"))


def _summary(report: dict) -> dict:
    after = report["after"]
    update = report["update_report"]
    return {
        "status": after["status"],
        "latest_local_trade_date": after["latest_local_trade_date"],
        "expected_data_date": after["expected_data_date"],
        "data_fresh": after["data_fresh"],
        "data_update_recommended": after["data_update_recommended"],
        "llm_enabled": after["llm_enabled"],
        "llm_provider_configured": after["llm_provider_configured"],
        "offline_eval_passed": after["offline_eval_passed"],
        "offline_eval_failed": after["offline_eval_failed"],
        "data_health": after["data_health"]["status"],
        "update_attempted": update is not None,
        "update_error": update.get("error") if isinstance(update, dict) else None,
        "warnings": after["warnings"][:8],
    }


if __name__ == "__main__":
    main()
