"""Agreement and Cohen's kappa for judge calibration."""

from __future__ import annotations


def cohen_kappa(labels: list[str], predictions: list[str]) -> float | None:
    """Chance-corrected agreement; None when there are no paired labels."""

    if not labels or len(labels) != len(predictions):
        return None
    count = len(labels)
    observed = sum(1 for left, right in zip(labels, predictions) if left == right) / count
    categories = set(labels) | set(predictions)
    expected = sum(
        (labels.count(category) / count) * (predictions.count(category) / count)
        for category in categories
    )
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return round((observed - expected) / (1 - expected), 4)


def agreement(labels: list[str], predictions: list[str]) -> float | None:
    if not labels or len(labels) != len(predictions):
        return None
    return round(
        sum(1 for left, right in zip(labels, predictions) if left == right) / len(labels),
        4,
    )


def summarize_calibration(
    cases: list[dict],
    predictions: dict[str, dict[str, str]],
) -> dict:
    """Compare human labels with judge verdicts per dimension and overall."""

    dimensions = (
        "intent_delivery",
        "factual_grounding",
        "source_attribution",
        "compliance",
        "presentation",
    )
    report: dict = {"dimensions": {}, "missing_predictions": []}
    all_labels: list[str] = []
    all_predictions: list[str] = []
    for dimension in dimensions:
        labels: list[str] = []
        predicted: list[str] = []
        for case in cases:
            case_id = str(case.get("case_id"))
            label = (case.get("labels") or {}).get(dimension)
            if label is None:
                continue
            if case_id not in predictions:
                report["missing_predictions"].append(case_id)
                continue
            labels.append(str(label))
            predicted.append(str(predictions[case_id].get(dimension, "unknown")))
        all_labels.extend(labels)
        all_predictions.extend(predicted)
        report["dimensions"][dimension] = {
            "count": len(labels),
            "agreement": agreement(labels, predicted),
            "kappa": cohen_kappa(labels, predicted),
        }
    report["overall"] = {
        "count": len(all_labels),
        "agreement": agreement(all_labels, all_predictions),
        "kappa": cohen_kappa(all_labels, all_predictions),
    }
    report["missing_predictions"] = sorted(set(report["missing_predictions"]))
    return report


def calibration_gate(
    summary: dict,
    *,
    minimum_cases: int = 30,
    minimum_kappa: float = 0.6,
) -> dict:
    """Require enough labeled cases and per-dimension chance-corrected agreement."""

    case_count = min(
        [int(item.get("count") or 0) for item in (summary.get("dimensions") or {}).values()]
        or [0]
    )
    weak = [
        name for name, item in (summary.get("dimensions") or {}).items()
        if item.get("kappa") is None or item["kappa"] < minimum_kappa
    ]
    return {
        "minimum_cases": minimum_cases,
        "minimum_kappa": minimum_kappa,
        "case_count": case_count,
        "weak_dimensions": weak,
        "passed": case_count >= minimum_cases and not weak,
    }
