import unittest

from app.services.relay_universe import is_relay_candidate_symbol, is_relay_research_security


class RelayUniverseTest(unittest.TestCase):
    # Regression scenario: excludes chinext and keeps supported a share boards.
    def test_excludes_chinext_and_keeps_supported_a_share_boards(self) -> None:
        self.assertFalse(is_relay_candidate_symbol("300189"))
        self.assertFalse(is_relay_candidate_symbol("301489"))
        self.assertTrue(is_relay_candidate_symbol("000001"))
        self.assertTrue(is_relay_candidate_symbol("002001"))
        self.assertTrue(is_relay_candidate_symbol("600001"))

    def test_bse_is_not_a_relay_candidate(self) -> None:
        for symbol in ("430047", "830799", "920096"):
            self.assertFalse(is_relay_candidate_symbol(symbol))

    def test_research_context_excludes_only_risk_warning_and_bse(self) -> None:
        for symbol, name in (
            ("000639", "ST西王"), ("600302", "*ST标准"),
            ("600001", "退市测试"), ("920096", "嘉晨智能"),
            ("430047", "测试"), ("830799", "测试"),
        ):
            self.assertFalse(is_relay_research_security(symbol, name))
        for symbol in ("000001", "300001", "688001"):
            self.assertTrue(is_relay_research_security(symbol, "正常股票"))
