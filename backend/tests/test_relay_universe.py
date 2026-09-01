import unittest

from app.services.relay_universe import is_relay_candidate_symbol


class RelayUniverseTest(unittest.TestCase):
    def test_excludes_chinext_and_keeps_supported_a_share_boards(self) -> None:
        self.assertFalse(is_relay_candidate_symbol("300189"))
        self.assertFalse(is_relay_candidate_symbol("301489"))
        self.assertTrue(is_relay_candidate_symbol("000001"))
        self.assertTrue(is_relay_candidate_symbol("002001"))
        self.assertTrue(is_relay_candidate_symbol("600001"))
