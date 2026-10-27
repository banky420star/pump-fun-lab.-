"""Behavioral checks for paper replay, with hand-calculated cashflows."""

import math
import unittest

from pumpfun_lab.engine import run_replay


def tick(ts_ms, price, mint="TEST", **fields):
    return {"ts_ms": ts_ms, "mint": mint, "price_sol": price, **fields}


class ReplayAccountingTests(unittest.TestCase):
    def test_roundtrip_deducts_fees_from_all_in_budget(self):
        # One SOL budget: .1 network + .9 / 1.1 notional => .9/1.1/1.1 tokens.
        # A later price of 2 returns (quantity * 2 * .9) - .1 = 1.2388429752.
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1),
                  tick(3000, 1.1), tick(4000, 2), tick(5000, 2)]
        result = run_replay(events, {
            "initial_balance_sol": 10, "position_sol": 1,
            "protocol_fee_bps": 1000, "slippage_bps": 0,
            "network_fee_sol": .1, "take_profit_bps": 1000,
        }, "momentum")
        self.assertEqual(result.get("closed_trade_count"), 1)
        self.assertAlmostEqual(result["cash_sol"], 10.238842975206612)
        self.assertAlmostEqual(result["net_pnl_sol"], .238842975206612)
        self.assertAlmostEqual(result["realized_pnl_sol"], .238842975206612)
        self.assertAlmostEqual(result["costs"]["protocol_fees_sol"], .2305785123966942)
        self.assertAlmostEqual(result["costs"]["network_fees_sol"], .2)
        self.assertEqual(result["win_rate"], 1)

    def test_signal_cannot_fill_on_the_same_event_even_with_zero_latency(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1)]
        result = run_replay(events, {"latency_ms": 0}, "momentum")
        self.assertEqual(result.get("fill_count"), 0)
        self.assertEqual(len(result["pending_orders"]), 1)
        result = run_replay(events + [tick(2000, 1.2)], {"latency_ms": 0}, "momentum")
        self.assertEqual(result["fill_count"], 1)
        self.assertEqual(result["fills"][0]["event_index"], 3)
        self.assertEqual(result["fills"][0]["signal_event_index"], 2)

    def test_higher_latency_uses_later_price_and_receives_fewer_tokens(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1),
                  tick(2500, 1.2), tick(3000, 1.5)]
        fast = run_replay(events, {"latency_ms": 500}, "momentum")
        slow = run_replay(events, {"latency_ms": 1000}, "momentum")
        self.assertEqual(fast.get("fill_count"), 1)
        self.assertEqual(slow.get("fill_count"), 1)
        self.assertEqual(fast["fills"][0]["observed_price_sol"], 1.2)
        self.assertEqual(slow["fills"][0]["observed_price_sol"], 1.5)
        self.assertLess(slow["fills"][0]["quantity_tokens"], fast["fills"][0]["quantity_tokens"])

    def test_zero_trades_has_no_invented_win_rate(self):
        result = run_replay([tick(0, 1)], {}, "momentum")
        self.assertEqual(result.get("closed_trade_count"), 0)
        self.assertEqual(result["fill_count"], 0)
        self.assertIsNone(result["win_rate"])
        self.assertEqual(result["net_pnl_sol"], 0)

    def test_slippage_reduces_quantity_and_sale_proceeds_without_double_charge(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1),
                  tick(3000, 1.1), tick(4000, 2), tick(5000, 2)]
        result = run_replay(events, {
            "position_sol": 1, "protocol_fee_bps": 0, "slippage_bps": 1000,
            "network_fee_sol": 0, "take_profit_bps": 1000,
        }, "momentum")
        self.assertEqual(result.get("closed_trade_count"), 1)
        self.assertAlmostEqual(result["fills"][0]["quantity_tokens"], .8264462809917354)
        self.assertAlmostEqual(result["cash_sol"], 10.487603305785123)
        self.assertAlmostEqual(result["costs"]["modeled_slippage_sol"], .256198347107438)
        self.assertEqual(result["costs"]["total_explicit_fees_sol"], 0)

    def test_unsellable_inventory_is_not_a_winner_or_a_fabricated_liquidation(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1),
                  tick(3000, 1.1), tick(4000, 100, sellable=False),
                  tick(5000, 100, sellable=False)]
        result = run_replay(events, {}, "momentum")
        self.assertEqual(result.get("closed_trade_count"), 0)
        self.assertIsNone(result["win_rate"])
        self.assertEqual(result["conservative_inventory_value_sol"], 0)
        self.assertAlmostEqual(result["net_pnl_sol"], -.05)
        self.assertEqual(result["realized_pnl_sol"], 0)
        self.assertEqual(result["open_positions"][0]["valuation_status"], "unsellable")
        self.assertTrue(any(r["reason"] == "unsellable" for r in result["rejections"]))

    def test_other_mint_observation_makes_old_inventory_stale(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1),
                  tick(3000, 1.1), tick(4000, 5), tick(10000, 1, "CLOCK")]
        result = run_replay(events, {}, "momentum")
        self.assertEqual(result.get("closed_trade_count"), 0)
        self.assertEqual(result["conservative_inventory_value_sol"], 0)
        self.assertAlmostEqual(result["cash_sol"], 9.95)
        self.assertEqual(result["open_positions"][0]["valuation_status"], "stale")
        self.assertEqual(result["open_positions"][0]["quote_age_ms"], 6000)
        self.assertGreater(result["max_drawdown_pct"], 0)

    def test_entry_signal_expiring_before_fill_is_rejected(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1), tick(8000, 1.2)]
        result = run_replay(events, {}, "momentum")
        self.assertEqual(result.get("fill_count"), 0)
        self.assertTrue(any(r["reason"] == "stale_buy_signal" for r in result["rejections"]))
        self.assertEqual(result["pending_orders"], [])

    def test_insufficient_balance_cannot_create_a_position(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1), tick(3000, 1.2)]
        result = run_replay(events, {"initial_balance_sol": .04}, "momentum")
        self.assertEqual(result.get("fill_count"), 0)
        self.assertEqual(result["cash_sol"], .04)
        self.assertTrue(any(r["reason"] == "insufficient_balance" for r in result["rejections"]))

    def test_pending_buys_reserve_balance_and_position_capacity(self):
        events = [tick(t, p, m) for t, p in [(0, 1), (1000, 1.05), (2000, 1.1), (3000, 1.2)]
                  for m in ["A", "B"]]
        result = run_replay(events, {"initial_balance_sol": .05}, "momentum")
        self.assertEqual(result.get("fill_count"), 1)
        self.assertAlmostEqual(result["cash_sol"], 0)
        capped = run_replay(events, {"max_positions": 1}, "momentum")
        self.assertEqual(capped["fill_count"], 1)
        self.assertTrue(any(r["reason"] == "max_positions" for r in capped["rejections"]))

    def test_insufficient_observed_liquidity_prevents_fill(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1),
                  tick(3000, 1.1, liquidity_sol=.001)]
        result = run_replay(events, {}, "momentum")
        self.assertEqual(result.get("fill_count"), 0)
        self.assertTrue(any(r["reason"] == "insufficient_liquidity" for r in result["rejections"]))

    def test_stop_loss_uses_later_observed_sale_price(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1),
                  tick(3000, 1.1), tick(4000, .5), tick(5000, .1)]
        result = run_replay(events, {}, "momentum")
        self.assertEqual(result.get("closed_trade_count"), 1)
        self.assertEqual(result["fills"][1]["observed_price_sol"], .1)
        self.assertEqual(result["trades"][0]["exit_reason"], "stop_loss")
        self.assertLess(result["realized_pnl_sol"], -.04)
        self.assertEqual(result["win_rate"], 0)

    def test_timeout_waits_for_next_executable_mint_event(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1), tick(3000, 1.1),
                  tick(5000, 1, "CLOCK"), tick(6000, 1.1)]
        result = run_replay(events, {"max_hold_ms": 2000}, "momentum")
        self.assertEqual(result.get("closed_trade_count"), 1)
        self.assertEqual(result["trades"][0]["exit_reason"], "max_hold")
        self.assertEqual(result["fills"][1]["fill_ts_ms"], 6000)

    def test_finite_extreme_balances_do_not_overflow_drawdown_percentage(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1),
                  tick(3000, 1.1), tick(4000, 1.1, sellable=False)]
        result = run_replay(events, {
            "initial_balance_sol": 1e308, "position_sol": 1e307,
            "network_fee_sol": 0, "protocol_fee_bps": 0, "slippage_bps": 0,
        }, "momentum")
        self.assertTrue(math.isfinite(result["max_drawdown_pct"]))
        self.assertAlmostEqual(result["max_drawdown_pct"], 10)

    def test_finite_extreme_trade_values_do_not_overflow_return_percentage(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1),
                  tick(3000, 1.1), tick(4000, .1), tick(5000, .1)]
        result = run_replay(events, {
            "initial_balance_sol": 1e308, "position_sol": 1e307,
            "network_fee_sol": 0, "protocol_fee_bps": 0, "slippage_bps": 0,
        }, "momentum")
        self.assertTrue(math.isfinite(result["trades"][0]["return_pct"]))
        self.assertAlmostEqual(result["trades"][0]["return_pct"], -90.9090909090909)


class ReplayCausalityTests(unittest.TestCase):
    def test_future_prices_cannot_change_prior_fills(self):
        prefix = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1), tick(3000, 1.2)]
        rising = run_replay(prefix + [tick(4000, 100)], {}, "momentum")
        falling = run_replay(prefix + [tick(4000, .001)], {}, "momentum")
        self.assertEqual(rising.get("fill_count"), 1)
        self.assertEqual(falling.get("fill_count"), 1)
        self.assertEqual(rising["fills"], falling["fills"])

    def test_two_prior_increasing_observations_are_required(self):
        result = run_replay([tick(0, 1), tick(1000, 2), tick(2000, 1.5), tick(3000, 1.7)], {}, "momentum")
        self.assertEqual(result.get("fill_count"), 0)
        self.assertEqual(result["pending_orders"], [])

    def test_old_history_cannot_create_fresh_momentum(self):
        events = [tick(0, 1), tick(10000, 1.1), tick(20000, 1.2), tick(21000, 1.3)]
        result = run_replay(events, {}, "momentum")
        self.assertEqual(result.get("fill_count"), 0)
        self.assertEqual(result["pending_orders"], [])

    def test_creator_filter_uses_supplied_prior_history_at_signal_time(self):
        events = [tick(0, 1), tick(1000, 1.05), tick(2000, 1.1),
                  tick(3000, 1.2, creator_prior_launches=2, creator_prior_success_rate=.8)]
        blocked = run_replay(events, {}, "creator_filter")
        self.assertEqual(blocked.get("fill_count"), 0)
        self.assertEqual(len(blocked["pending_orders"]), 1)
        valid = [dict(e, creator_prior_launches=2, creator_prior_success_rate=.8) for e in events]
        allowed = run_replay(valid, {}, "creator_filter")
        self.assertEqual(allowed["fill_count"], 1)
        self.assertTrue(any(r["reason"] == "creator_history_gate" for r in blocked["rejections"]))

    def test_pre_graduation_requires_progress_in_range_on_signal(self):
        events = [tick(0, 1, progress=.5), tick(1000, 1.05, progress=.6),
                  tick(2000, 1.1, progress=.7), tick(3000, 1.2, progress=.9)]
        blocked = run_replay(events, {}, "pre_graduation")
        self.assertEqual(blocked.get("fill_count"), 0)
        self.assertEqual(len(blocked["pending_orders"]), 1)
        allowed = run_replay([dict(e, progress=.9) for e in events], {}, "pre_graduation")
        self.assertEqual(allowed["fill_count"], 1)

    def test_synthetic_source_is_explicit(self):
        result = run_replay([], {"source_label": "synthetic"}, "momentum")
        self.assertEqual(result.get("source_label"), "synthetic")
        self.assertEqual(result["mode"], "paper_replay")

    def test_source_label_preserves_descriptive_provenance(self):
        try:
            result = run_replay([], {"source_label": "synthetic engineering fixture (not market history)"}, "momentum")
        except ValueError as exc:
            self.fail(f"A descriptive provenance label must be accepted: {exc}")
        self.assertEqual(result["source_label"], "synthetic engineering fixture (not market history)")


class ReplayValidationTests(unittest.TestCase):
    def test_invalid_event_values_and_order_are_rejected(self):
        invalid_events = [
            [tick(0, float("nan"))], [tick(0, float("inf"))], [tick(0, 0)],
            [tick(0, -1)], [tick(-1, 1)], [tick(True, 1)], [tick(1.5, 1)],
            [tick(0, 1, "")], [tick(0, 1, "  ")], [tick(0, True)],
            [tick(1000, 1), tick(999, 1)], [tick(0, 1, liquidity_sol=0)],
            [tick(0, 1, creator_prior_launches=-1)],
            [tick(0, 1, creator_prior_success_rate=1.1)],
            [tick(0, 1, creator_prior_success_rate=float("nan"))],
            [tick(0, 1, progress=-.1)], [tick(0, 1, sellable="true")],
            [{"ts_ms": 0, "mint": "X"}],
        ]
        for events in invalid_events:
            with self.subTest(events=events), self.assertRaises(ValueError):
                run_replay(events, {}, "momentum")

    def test_invalid_configuration_is_rejected(self):
        for config in [{"latency_ms": -1}, {"max_positions": 0},
                       {"slippage_bps": 10000}, {"protocol_fee_bps": -1},
                       {"position_sol": 0}, {"initial_balance_sol": float("nan")},
                       {"max_quote_age_ms": 0}, {"network_fee_sol": -.1},
                       {"position_sol": .05, "network_fee_sol": .05},
                       {"pre_graduation_min_progress": .9, "pre_graduation_max_progress": .8},
                       {"unknown_option": 1}]:
            with self.subTest(config=config), self.assertRaises(ValueError):
                run_replay([], config, "momentum")
        with self.assertRaises(ValueError):
            run_replay([], {}, "unrecognized_strategy")


if __name__ == "__main__":
    unittest.main()
