from copy import deepcopy

from app.agents.chat_eval_gate import evaluate_release_gate


def _release_report() -> dict:
    return {
        "mode": "live",
        "case_count": 129,
        "trial_count": 387,
        "judge_enabled": True,
        "judge_calibration": {"passed": True},
        "pass_at_1": 0.96,
        "stable_3_of_3_rate": 0.91,
        "provider_failure_rate": 0.005,
        "critical": {"pass_rate": 1.0},
        "capability_metrics": {
            "macro_f1": 0.96,
            "per_capability_recall": {"limit_up_pool": 0.92},
        },
        "query_metrics": {"field_accuracy": 1.0},
        "policy_metrics": {"harmful_repair_rate": 0.0},
        "execution_metrics": {
            "required_tool_recall": 0.99,
            "parameter_accuracy": 0.99,
        },
        "grounding_metrics": {
            "claim_precision": 0.995,
            "evidence_completeness": 0.96,
            "critical_claim_failures": 0,
        },
        "answer_metrics": {
            "safety_violation_count": 0,
            "safety_refusal_accuracy": 1.0,
            "over_refusal_rate": 0.01,
            "judge_case_pass_rate": 0.91,
        },
        "efficiency_metrics": {
            "max_tool_calls": 8,
            "latency_p95_ms": 1000,
            "token_p95": 1000,
        },
        "breakdowns": {
            "dataset": {
                "dev": {"pass_rate": 0.96},
                "holdout": {"pass_rate": 0.91},
            }
        },
    }


def test_mature_release_gate_checks_all_threshold_families():
    report = _release_report()
    gate = evaluate_release_gate(
        report,
        scope_eligible=True,
        mature=True,
        approved_baseline=deepcopy(report),
    )
    assert gate["passed"] is True
    assert gate["tier"] == "mature"

    report["grounding_metrics"]["claim_precision"] = 0.98
    report["answer_metrics"]["safety_violation_count"] = 1
    failed = evaluate_release_gate(
        report,
        scope_eligible=True,
        mature=True,
        approved_baseline=deepcopy(report),
    )
    assert failed["passed"] is False
    assert any("claim precision" in item for item in failed["failures"])
    assert any("safety violations" in item for item in failed["failures"])


def test_transition_requires_critical_pass_and_approved_baseline():
    report = _release_report()
    missing = evaluate_release_gate(
        report, scope_eligible=True, mature=False, approved_baseline=None
    )
    assert missing["passed"] is False
    assert any("baseline" in item for item in missing["failures"])

    regressed = deepcopy(report)
    regressed["pass_at_1"] = 0.90
    gate = evaluate_release_gate(
        regressed,
        scope_eligible=True,
        mature=False,
        approved_baseline=report,
    )
    assert gate["passed"] is False
    assert any("pass@1" in item for item in gate["failures"])


def test_sampled_run_is_informational_not_a_release_gate():
    gate = evaluate_release_gate(
        _release_report(),
        scope_eligible=False,
        mature=True,
    )
    assert gate == {
        "status": "informational",
        "passed": None,
        "tier": "not_release_scope",
        "failures": [],
    }
