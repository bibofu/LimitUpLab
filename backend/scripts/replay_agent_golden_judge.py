"""Validate fixed Agent traces or replay their Judge calls in a separate report."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.golden.replay import load_replay, replay_report


def turn_key(value):
    try:
        case_id, trial, turn = value.split(":")
        trial, turn = int(trial), int(turn)
        if not case_id or trial < 1 or turn < 0:
            raise ValueError
        return case_id, trial, turn
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use case_id:positive_trial:zero_based_turn") from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=("validate", "live"), default="validate")
    parser.add_argument("--turn", type=turn_key, action="append", help="Exact key; omit to replay every turn")
    parser.add_argument("--max-model-calls", type=int, default=120)
    args = parser.parse_args(argv)
    if args.max_model_calls < 1:
        parser.error("max-model-calls must be positive")
    try:
        loaded = load_replay(args.input, selected_turns=args.turn)
        if args.mode == "validate":
            print(json.dumps({"mode": "validate", "source_sha256": loaded["source_sha256"],
                "fixed_inputs_sha256": loaded["fixed_inputs_sha256"], "turns": len(loaded["inputs"]),
                "model_calls": 0, "keys": [i["key"] for i in loaded["inputs"]]}, ensure_ascii=False))
            return 0
        if args.output is None:
            parser.error("live replay requires a new --output directory")
        from app.config import configure_runtime_environment
        from app.services.llm_provider import DisabledLLMProvider, get_llm_provider
        configure_runtime_environment()
        provider = get_llm_provider()
        if isinstance(provider, DisabledLLMProvider):
            parser.error("No configured model; no requests were made")
        report = replay_report(args.input, args.output, provider,
                               max_calls=args.max_model_calls, selected_turns=args.turn)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(f"Replay validation/storage error ({type(error).__name__})")
    print(json.dumps({"output": str(args.output.resolve()), "summary": report["summary"]}, ensure_ascii=False))
    return 0 if report["summary"]["execution_complete"] and all(
        item["verdict"] == "pass" for item in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
