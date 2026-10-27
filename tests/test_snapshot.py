"""Tests pin arithmetic and source limits using fabricated demo data."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from pumpfun_lab.snapshot import build_snapshot_report, render_snapshot_markdown


ROOT = Path(__file__).resolve().parents[1]


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.profile = ROOT / "data/fixtures/profile_snapshot.json"
        self.trades = ROOT / "data/fixtures/top_trades.csv"
        self.created = ROOT / "data/fixtures/created_coins.csv"

    def report(self):
        return build_snapshot_report(self.profile, self.trades, self.created)

    def test_top_list_is_not_presented_as_complete_wallet_performance(self):
        report = self.report()
        self.assertEqual(report.get("provenance", {}).get("dataset_kind"), "selected_per_coin_summaries")
        self.assertIs(report.get("provenance", {}).get("complete_transaction_history"), False)
        self.assertIsNone(report.get("metrics", {}).get("overall_win_rate_pct"))
        self.assertIsNone(report.get("metrics", {}).get("true_roi_pct"))

    def test_duplicate_tickers_are_preserved_as_distinct_mints(self):
        report = self.report()
        rows = report.get("top_trades", [])
        self.assertEqual(len(rows), 50)
        repeated = [row for row in rows if row["label"] == "SYNTH_REPEAT"]
        self.assertEqual(len(repeated), 3)
        self.assertEqual(len({row["mint"] for row in repeated}), 3)

    def test_rounded_spending_is_not_promoted_to_exact_cost_basis(self):
        rows = self.report().get("top_trades", [])
        compressed = [row for row in rows if row["rank"] == 17]
        self.assertEqual(len(compressed), 1)
        self.assertEqual(compressed[0]["spent_usd_display_estimate"], "2500.00")
        self.assertEqual(compressed[0]["spent_displayed"], "$2.50K")
        self.assertIs(compressed[0]["spent_is_exact_cost_basis"], False)

    def test_headline_reconciliation_keeps_the_component_discrepancy(self):
        reconciliation = self.report().get("reconciliation", {})
        self.assertEqual(reconciliation.get("realized_plus_unrealized_usd"), "975.00")
        self.assertEqual(reconciliation.get("headline_minus_components_usd"), "0.50")
        self.assertIs(reconciliation.get("aggregate_and_subset_scope_verified"), False)
        self.assertIsNone(reconciliation.get("proven_total_losses_usd"))

    def test_exact_sum_and_subset_concentration_use_reported_profits(self):
        report = self.report()
        metrics = report.get("metrics", {})
        self.assertEqual(metrics.get("top_list_profit_usd"), "1275.00")
        self.assertEqual(metrics.get("top5_profit_usd"), "240.00")
        self.assertEqual(metrics.get("top10_profit_usd"), "455.00")
        self.assertEqual(metrics.get("top5_share_of_top_list_pct"), "18.82")
        self.assertEqual(metrics.get("top10_share_of_top_list_pct"), "35.69")
        self.assertEqual(report.get("reconciliation", {}).get("realized_minus_top_list_usd"), "-275.00")

    def test_creation_matching_uses_mint_and_does_not_guess_other_creators(self):
        result = self.report().get("created_coin_matches", {})
        self.assertEqual(result.get("matched_top_trade_rows"), 8)
        self.assertEqual(result.get("unclassified_top_trade_rows"), 42)
        report = self.report()
        twins = [row for row in report.get("top_trades", []) if row["label"] == "SYNTH_TWIN"]
        self.assertEqual(len(twins), 2)
        self.assertTrue(all(row["creation_classification"] == "not_established" for row in twins))

    def test_report_can_be_serialized_without_nan_and_renders_angle_bracket_name(self):
        report = self.report()
        self.assertIsInstance(json.dumps(report, allow_nan=False), str)
        markdown = render_snapshot_markdown(report)
        self.assertIn("&lt;demo&gt;", markdown)
        self.assertNotIn("| SYNTH_<demo> |", markdown)

    def test_synthetic_source_is_explicit_in_the_report_and_rendered_output(self):
        report = self.report()
        self.assertEqual(report["provenance"]["source"], "synthetic_demo")
        self.assertIs(report["provenance"]["independently_verified"], False)
        self.assertEqual(report["created_coin_matches"]["verification"], "synthetic_demo_only")
        markdown = render_snapshot_markdown(report)
        self.assertIn("Source: synthetic demo", markdown)
        self.assertIn("fictional", markdown)
        self.assertIn("SYNTHETIC_WALLET_NOT_ON_CHAIN", markdown)
        self.assertNotIn("Source: user-pasted", markdown)
        self.assertFalse(any(row["date_year_inferred"] for row in report["top_trades"]))

    def test_source_description_and_observation_time_follow_imported_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = json.loads(self.profile.read_text(encoding="utf-8"))
            profile.update({
                "source": "offline export example",
                "source_description": "Fabricated offline export for tests.",
                "observed_at": "1999-04-04T00:00:00Z",
                "dates_are_fictional": False,
                "independently_verified": True,
            })
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            report = build_snapshot_report(path, self.trades, self.created)
        markdown = render_snapshot_markdown(report)
        self.assertIn("Source: offline export example", markdown)
        self.assertIn("Fabricated offline export for tests.", markdown)
        self.assertIn("1999-04-04T00:00:00Z", markdown)
        self.assertIs(report["provenance"]["independently_verified"], False)

    def test_return_and_date_limitations_use_the_supplied_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = json.loads(self.profile.read_text(encoding="utf-8"))
            profile["headline_return_pct_reported"] = "37.25"
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            report = build_snapshot_report(path, self.trades, self.created)
        self.assertIn("37.25%", " ".join(report["limitations"]))
        markdown = render_snapshot_markdown(report)
        self.assertIn("fictional", markdown)
        self.assertNotIn("Dates omit their year", markdown)

    def test_nonfinite_profit_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.csv"
            with self.trades.open(encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
            rows[0]["profit_usd_reported"] = "NaN"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(ValueError):
                build_snapshot_report(self.profile, path, self.created)

    def test_duplicate_mint_is_rejected_instead_of_double_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.csv"
            with self.trades.open(encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
            rows[1]["mint"] = rows[0]["mint"]
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(ValueError):
                build_snapshot_report(self.profile, path, self.created)

    def test_duplicate_profit_header_cannot_silently_replace_a_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["rank", "mint", "label", "profit_usd_reported",
                                 "profit_usd_reported", "spent_displayed",
                                 "roi_pct_displayed", "date_displayed"])
                writer.writerow([1, "mint-a", "Token", "-100.00", "100.00",
                                 "$100", "100", "2000-01-01"])
            with self.assertRaises(ValueError):
                build_snapshot_report(self.profile, path, self.created)

    def test_empty_or_whitespace_header_cannot_hide_an_unlabeled_column(self):
        with self.created.open(encoding="utf-8", newline="") as source:
            original = list(csv.reader(source))
        for header in ("", "   "):
            with self.subTest(header=repr(header)), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "created.csv"
                with path.open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(original[0] + [header])
                    writer.writerows(row + ["unlabeled data"] for row in original[1:])
                with self.assertRaises(ValueError):
                    build_snapshot_report(self.profile, self.trades, path)


if __name__ == "__main__":
    unittest.main()
