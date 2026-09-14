from app.agent_eval.empty_acceptance import calibration_samples
from app.agent_eval.event_extractor import ExtractedBusiness


def test_empty_calibration_contains_variants_and_counterexamples():
    samples = calibration_samples("2026-09-11")
    names = {item[0] for item in samples}
    assert names == {"plain", "spaced", "approx", "distractor", "wrong_count", "wrong_date"}
    assert any(item[3] != 0 for item in samples)
    assert any(item[2] != "2026-09-11" for item in samples)


def test_business_extraction_schema_distinguishes_scoped_and_market_counts():
    properties = ExtractedBusiness.model_json_schema()["properties"]
    assert "全市场" in properties["limit_up_count"]["description"]
    assert "筛选" in properties["matched_count"]["description"]
