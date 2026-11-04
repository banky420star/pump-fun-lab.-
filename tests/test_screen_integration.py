"""Regression checks for the experimental paper-only risk settings."""
import json
from pathlib import Path
import unittest

from pumpfun_lab.engine import run_replay


def tick(ts_ms, price, **extra):
    return {"ts_ms": ts_ms, "mint": "TEST", "price_sol": price, **extra}


def sample(**signal_data):
    return [
        tick(0, 1),
        tick(1000, 1.05),
        tick(2000, 1.1, **signal_data),
        tick(3000, 1.2),
    ]


SETTINGS = {
    "initial_balance_sol": 10,
    "position_fraction": 0.02,
    "enforce_holder_screen": True,
    "stop_loss_bps": 3500,
}


class ScreeningReplayTests(unittest.TestCase):
    def test_screening_and_dynamic_allocation_reach_fill(self):
        data = sample(top10_holder_fraction=.25, developer_holder_fraction=.05)
        result = run_replay(data, SETTINGS, "momentum")
        self.assertEqual(result["fill_count"], 1)
        self.assertAlmostEqual(result["fills"][0]["net_cashflow_sol"], -.2)
        self.assertAlmostEqual(result["cash_sol"], 9.8)
        self.assertTrue(result["decision_journal"][0]["approved"])
        self.assertAlmostEqual(result["decision_journal"][0]["allocated_budget_sol"], .2)

    def test_missing_ownership_fails_closed_and_is_logged(self):
        result = run_replay(sample(), SETTINGS, "momentum")
        self.assertEqual(result["fill_count"], 0)
        self.assertEqual(result["pending_orders"], [])
        self.assertIn("missing_or_invalid_top10_concentration", result["rejections"][0]["reason"])
        self.assertIn("missing_or_invalid_developer_ownership", result["decision_journal"][0]["reason"])

    def test_concentration_above_limit_is_rejected(self):
        data = sample(top10_holder_fraction=.251, developer_holder_fraction=.051)
        result = run_replay(data, SETTINGS, "momentum")
        self.assertEqual(result["fill_count"], 0)
        self.assertIn("top10_concentration_exceeded", result["rejections"][0]["reason"])
        self.assertIn("developer_ownership_exceeded", result["rejections"][0]["reason"])

    def test_legacy_fixed_budget_remains_available(self):
        result = run_replay(sample(), {}, "momentum")
        self.assertEqual(result["fill_count"], 1)
        self.assertAlmostEqual(result["fills"][0]["net_cashflow_sol"], -.05)

    def test_stop_is_a_trigger_not_a_guaranteed_price(self):
        data = sample(top10_holder_fraction=.1, developer_holder_fraction=.01)
        data += [tick(4000, .5), tick(5000, .1)]
        result = run_replay(data, SETTINGS, "momentum")
        self.assertEqual(result["closed_trade_count"], 1)
        self.assertEqual(result["trades"][0]["exit_reason"], "stop_loss")
        self.assertEqual(result["fills"][-1]["observed_price_sol"], .1)

    def test_rejects_invalid_percent_inputs(self):
        with self.assertRaises(ValueError):
            run_replay(sample(top10_holder_fraction=25, developer_holder_fraction=.01), SETTINGS, "momentum")
        with self.assertRaises(ValueError):
            run_replay(sample(top10_holder_fraction=.2, developer_holder_fraction=float("nan")), SETTINGS, "momentum")

    def test_main_configuration_has_paper_guards(self):
        config = json.loads(Path("config/paper.json").read_text(encoding="utf-8"))
        self.assertEqual(config["position_fraction"], 0.02)
        self.assertTrue(config["enforce_holder_screen"])
        self.assertEqual(config["top10_holder_limit"], .25)
        self.assertEqual(config["developer_holder_limit"], .05)
        self.assertEqual(config["stop_loss_bps"], 3500)


if __name__ == "__main__":
    unittest.main()
