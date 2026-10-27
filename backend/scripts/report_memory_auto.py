"""Aggregate safe_auto shadow/activation decisions without exposing memory text."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import configure_runtime_environment
from app.database import connect_readonly


def summarize_job_results(result_jsons: list[str]) -> dict:
    """Aggregate outcomes and policy reasons from committed extraction jobs."""

    modes = Counter()
    outcomes = Counter()
    reasons = Counter()
    judge_decisions = Counter()
    judge_risks = Counter()
    policies = Counter()
    jobs = 0
    for raw in result_jsons:
        try:
            result = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(result, dict):
            continue
        jobs += 1
        modes[str(result.get("auto_mode") or "unknown")] += 1
        policies[str(result.get("auto_policy_version") or "unknown")] += 1
        decisions = result.get("auto_decisions")
        if isinstance(decisions, list):
            for decision in decisions:
                if not isinstance(decision, dict):
                    continue
                outcomes[str(decision.get("outcome") or "unknown")] += 1
                for reason in decision.get("reasons") or []:
                    reasons[str(reason)] += 1
                if decision.get("judge_decision"):
                    judge_decisions[str(decision["judge_decision"])] += 1
                if decision.get("judge_risk"):
                    judge_risks[str(decision["judge_risk"])] += 1
            continue
        # Older jobs predate auto_decisions; keep their aggregate counters useful.
        outcomes["activated"] += len(result.get("auto_activated_ids") or [])
        outcomes["shadow"] += len(result.get("auto_shadow_eligible_ids") or [])
        outcomes["held"] += int(result.get("auto_held_count") or 0)
    return {
        "jobs": jobs,
        "modes": dict(sorted(modes.items())),
        "policies": dict(sorted(policies.items())),
        "outcomes": dict(sorted(outcomes.items())),
        "reasons": dict(sorted(reasons.items())),
        "judge_decisions": dict(sorted(judge_decisions.items())),
        "judge_risks": dict(sorted(judge_risks.items())),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args(argv)
    configure_runtime_environment()
    with connect_readonly() as db:
        rows = db.execute("""SELECT result_json FROM memory_extraction_jobs
            WHERE created_at>=clock_timestamp()-? * interval '1 day'
              AND result_json IS NOT NULL
            ORDER BY created_at""", (max(1, min(args.days, 90)),)).fetchall()
    print(json.dumps(summarize_job_results([row["result_json"] for row in rows]),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
