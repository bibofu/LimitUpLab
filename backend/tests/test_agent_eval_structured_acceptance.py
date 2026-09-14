from app.agent_eval.structured_acceptance import count_samples


def test_count_calibration_covers_language_and_fact_counterexamples():
    samples = count_samples("2026-09-11", 40)
    assert {sample[0] for sample in samples} == {"plain", "spaced", "approx", "wrong_count", "wrong_date", "multi"}
    assert any(claim[3] == "approximate" for _, _, claims in samples for claim in claims)
    assert any(len(claims) == 2 for _, _, claims in samples)
    assert any(claim[0] != "2026-09-11" for _, _, claims in samples for claim in claims)
    assert any(claim[2] != 40 for _, _, claims in samples for claim in claims if claim[1] == "limit_up_count")
