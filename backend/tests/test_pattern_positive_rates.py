import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('positive_rates', Path(__file__).parents[1] / 'scripts/research_pattern_positive_rates.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


# Regression scenario: friction applies to both cash flows.
def test_friction_applies_to_both_cash_flows():
    assert abs(module.adjusted_pct(10, .6) - (1.1 * .997 / 1.003 - 1) * 100) < 1e-10
    assert module.adjusted_pct(0, .3) < 0
    assert abs(module.adjusted_pct(10, 0) - 10) < 1e-10


# Regression scenario: positive rate counts zero as nonpositive.
def test_positive_rate_counts_zero_as_nonpositive():
    result = module.stats([{'r5': 0}, {'r5': 2}, {'r5': -3}], cost=0)
    assert result['positive_n'] == 1
    assert abs(result['average_positive_pct'] - 2) < 1e-10
    assert abs(result['average_negative_pct'] + 3) < 1e-10


# Regression scenario: matched rate excludes self and wrong date or age.
def test_matched_rate_excludes_self_and_wrong_date_or_age():
    signal = {'symbol': 's', 'signal_date': 'd', 'age': '2', 'r5': '5'}
    pool = [signal, {'symbol': 'a', 'signal_date': 'd', 'age': '2', 'r5': '-3'},
            {'symbol': 'b', 'signal_date': 'd', 'age': '1', 'r5': '10'}]
    result = module.matched_probability([signal], pool, ['d'])
    assert result['difference_pp'] == 100
    assert result['ci95_pp'] is None
