"""Live collection integration: evidence boundaries, saved output, and exit codes."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pumpfun_lab.cli import main, parser


ROOT = Path(__file__).resolve().parents[1]


def capture_fixture(mode="discovery", observations=None, errors=None):
    return {
        "schema_version": 1,
        "source": "PumpPortal live websocket",
        "transport": "websockets",
        "mode": mode,
        "started_at_utc": "2000-01-01T00:00:00+00:00",
        "ended_at_utc": "2000-01-01T00:01:30+00:00",
        "duration_seconds": 90,
        "connected": not bool(errors),
        "market_data_received": not bool(errors),
        "counts": {"messages": 2, "new_token": 2, "migration": 0,
                   "buy": 0, "sell": 0, "invalid": 0, "duplicates": 0},
        "subscribed_mints": [],
        "events": [],
        "observations": observations or [],
        "end_reason": "connection_error" if errors else "duration_limit",
        "errors": errors or [],
        "completeness": {"history_complete": False},
        "assumptions": [],
    }


class LiveCliTests(unittest.TestCase):
    def test_live_command_defaults_cannot_start_metered_data(self):
        err = StringIO()
        with redirect_stderr(err):
            try:
                args = parser().parse_args(["live"])
            except SystemExit:
                self.fail("The live command must expose safe discovery defaults")
        self.assertEqual(args.mode, "discovery")
        self.assertFalse(args.accept_data_charges)
        self.assertEqual(args.strategy, "momentum")
        self.assertLessEqual(args.duration_seconds, 900)

    def run_cli(self, args):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return main(args)

    def test_discovery_report_does_not_present_zero_profit_as_a_strategy_test(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("pumpfun_lab.live.capture_market", return_value=capture_fixture()):
                rc = self.run_cli(["live", "--output", directory])
            report = json.loads((Path(directory) / "report.json").read_text())
            self.assertEqual(rc, 0)
            self.assertEqual(report["replays"], [])
            self.assertFalse(report["evaluation"]["strategy_tested"])
            self.assertFalse(report["evaluation"]["profitability_established"])
            self.assertEqual((Path(directory) / "events.jsonl").read_text(), "")
            self.assertTrue((Path(directory) / "report.html").exists())

    def test_failed_connection_preserves_report_and_has_nonzero_status(self):
        failure = capture_fixture(errors=[{"code": "connection_failed"}])
        with tempfile.TemporaryDirectory() as directory:
            with patch("pumpfun_lab.live.capture_market", return_value=failure):
                rc = self.run_cli(["live", "--output", directory])
            report = json.loads((Path(directory) / "report.json").read_text())
            self.assertEqual(rc, 1)
            self.assertFalse(report["capture"]["connected"])
            self.assertFalse(report["evaluation"]["strategy_tested"])

    def test_handshake_without_market_events_does_not_pass_live_evidence_check(self):
        empty = capture_fixture()
        empty["market_data_received"] = False
        empty["counts"] = {"messages": 2, "new_token": 0, "migration": 0}
        with tempfile.TemporaryDirectory() as directory:
            with patch("pumpfun_lab.live.capture_market", return_value=empty):
                rc = self.run_cli(["live", "--output", directory])
            self.assertEqual(rc, 1)

    def test_observed_prices_use_real_paper_engine_and_retain_cost_uncertainty(self):
        events = [{"ts_ms": n * 1000, "mint": "SYNTH_LIVE_TEST",
                   "price_sol": price} for n, price in enumerate([1, 1.03, 1.10, 1.11, 1.50, 1.60])]
        capture = capture_fixture("trades", events)
        with tempfile.TemporaryDirectory() as directory:
            with patch("pumpfun_lab.live.capture_market", return_value=capture):
                rc = self.run_cli(["live", "--mode", "trades", "--accept-data-charges",
                                   "--config", str(ROOT / "config/paper.json"), "--output", directory])
            report = json.loads((Path(directory) / "report.json").read_text())
            self.assertEqual(rc, 0)
            self.assertTrue(report["evaluation"]["strategy_tested"])
            self.assertFalse(report["evaluation"]["profitability_established"])
            self.assertIsNone(report["evaluation"]["actual_data_cost_sol"])
            self.assertEqual(len(report["replays"]), 1)
            replay = report["replays"][0]
            self.assertEqual(replay["strategy"], "momentum")
            self.assertEqual(replay["closed_trade_count"], 1)
            self.assertEqual(len(replay["fills"]), 2)
            self.assertNotIn("synthetic engineering fixture", replay["source_label"])
            saved = [json.loads(line) for line in (Path(directory) / "events.jsonl").read_text().splitlines()]
            self.assertEqual(saved, events)

    def test_discovery_does_not_forward_a_secret_from_the_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"PUMPPORTAL_API_KEY": "TEST_ONLY_CREDENTIAL"}):
                with patch("pumpfun_lab.live.capture_market", return_value=capture_fixture()) as capture:
                    self.run_cli(["live", "--output", directory])
            # This is the boundary to an external data provider, not a mock-only contract.
            self.assertIsNone(capture.call_args.kwargs["api_key"])
            self.assertNotIn("TEST_ONLY_CREDENTIAL", (Path(directory) / "report.json").read_text())

    def test_bad_paper_settings_stop_before_paid_collection_can_begin(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "bad.json"
            config.write_text('{"slippage_bps": 10000}')
            with patch("pumpfun_lab.live.capture_market", return_value=capture_fixture()) as capture:
                rc = self.run_cli(["live", "--mode", "trades", "--accept-data-charges",
                                   "--config", str(config), "--output", directory])
            self.assertEqual(rc, 2)
            capture.assert_not_called()

    def test_replay_failure_preserves_the_actual_capture_and_never_reports_a_gain(self):
        events = [{"ts_ms": n * 1000, "mint": "SYNTH_LIVE_TEST",
                   "price_sol": price} for n, price in enumerate([1, 1.03, 1.1, 1e-320])]
        observed = capture_fixture("trades", events)
        with tempfile.TemporaryDirectory() as directory:
            with patch("pumpfun_lab.live.capture_market", return_value=observed):
                rc = self.run_cli(["live", "--mode", "trades", "--accept-data-charges",
                                   "--output", directory])
            report = json.loads((Path(directory) / "report.json").read_text())
            self.assertEqual(rc, 1)
            self.assertEqual(report["capture"]["observations"], events)
            self.assertEqual(report["replays"], [])
            self.assertTrue(report["evaluation"]["errors"])
            self.assertFalse(report["evaluation"]["strategy_tested"])

    def test_injected_observations_cannot_be_rendered_as_real_market_evidence(self):
        from pumpfun_lab.live_report import build_live_report
        for transport in ("injected", None):
            with self.subTest(transport=transport):
                observed = capture_fixture("trades", [{"ts_ms": 1, "mint": "SYNTH_TEST", "price_sol": 1}])
                observed["transport"] = transport
                report, markdown = build_live_report(observed, {}, "momentum")
                self.assertFalse(report["evaluation"].get("live_market_evidence", True))
                self.assertIn("unverified", report["replays"][0]["source_label"].lower())
                self.assertNotIn("Real feed observations were collected", markdown)

    def test_report_exposes_gap_after_last_price_instead_of_valuing_at_capture_end(self):
        from pumpfun_lab.live_report import build_live_report
        observed = capture_fixture("trades", [{"ts_ms": 946684810000, "mint": "SYNTH_TEST", "price_sol": 1}])
        report, markdown = build_live_report(observed, {}, "momentum")
        self.assertEqual(report["evaluation"].get("paper_valuation_ts_ms"), 946684810000)
        self.assertEqual(report["evaluation"].get("unobserved_tail_ms"), 80000)
        self.assertIn("80.000 seconds", markdown)


if __name__ == "__main__":
    unittest.main()
