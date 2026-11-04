import unittest
from decimal import Decimal
from pumpfun_lab.risk_screen import screen_token


class ScreenTokenTests(unittest.TestCase):
    def test_thresholds_are_inclusive(self):
        result = screen_token({"top10_holder_fraction": "0.25", "developer_holder_fraction": "0.05"}, "10")
        self.assertTrue(result.eligible)
        self.assertEqual(result.position_sol, Decimal("0.20"))

    def test_excess_concentration_rejected(self):
        result = screen_token({"top10_holder_fraction": "0.251", "developer_holder_fraction": "0.051"}, 10)
        self.assertFalse(result.eligible)
        self.assertEqual(len(result.reasons), 2)
        self.assertEqual(result.position_sol, 0)

    def test_missing_evidence_fails_closed(self):
        self.assertFalse(screen_token({}, 10).eligible)

    def test_invalid_and_nonfinite_rejected(self):
        self.assertFalse(screen_token({"top10_holder_fraction": "NaN", "developer_holder_fraction": 0}, 10).eligible)
        self.assertFalse(screen_token({"top10_holder_fraction": -1, "developer_holder_fraction": 0}, 10).eligible)
        self.assertFalse(screen_token({"top10_holder_fraction": 0, "developer_holder_fraction": 0}, "Infinity").eligible)

    def test_percentage_points_not_accepted(self):
        self.assertFalse(screen_token({"top10_holder_fraction": 25, "developer_holder_fraction": 5}, 10).eligible)


if __name__ == "__main__":
    unittest.main()
