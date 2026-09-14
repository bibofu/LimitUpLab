from app.agent_eval.semantic_acceptance import semantic_samples


def test_semantic_calibration_has_positive_and_adversarial_examples():
    for adapter in ("clarify_missing_entity", "refuse_trade_guarantee"):
        samples = semantic_samples(adapter)
        assert len(samples) >= 6
        assert {item[2] for item in samples} == {"pass", "fail"}
        assert len({item[0] for item in samples}) == len(samples)
