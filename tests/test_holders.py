"""Holder snapshot accounting must never approve incomplete or ambiguous evidence."""
import json
from pathlib import Path
import tempfile
import unittest

from pumpfun_lab.cli import main
from pumpfun_lab.holders import analyze_holder_snapshot


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/synthetic_holders.json"


def fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class HolderScreenTests(unittest.TestCase):
    def test_complete_fictional_snapshot_is_screened(self):
        report = analyze_holder_snapshot(fixture(), equity_sol="10")
        self.assertEqual(report["account_count"], 50)
        self.assertEqual(report["distinct_owner_count"], 50)
        self.assertEqual(report["top10_holder_fraction"], "0.2")
        self.assertEqual(report["developer_holder_fraction"], "0.02")
        self.assertTrue(report["decision"]["eligible"])
        self.assertEqual(report["decision"]["virtual_position_sol"], "0.20")

    def test_token_accounts_aggregate_by_declared_owner(self):
        data = fixture()
        data["token_accounts"][1]["owner"] = data["developer_owners"][0]
        report = analyze_holder_snapshot(data)
        self.assertEqual(report["distinct_owner_count"], 49)
        self.assertEqual(report["top10_holder_fraction"], "0.22")
        self.assertEqual(report["developer_holder_fraction"], "0.04")

    def test_partial_supply_cannot_pass_screen(self):
        data = fixture()
        data["token_accounts"].pop()
        with self.assertRaisesRegex(ValueError, "do not equal"):
            analyze_holder_snapshot(data)

    def test_declared_incomplete_and_unverified_developer_are_rejected(self):
        for key in ("accounts_complete", "developer_owners_verified"):
            data = fixture()
            data[key] = False
            with self.subTest(key=key), self.assertRaises(ValueError):
                analyze_holder_snapshot(data)
        data = fixture()
        data["developer_owners"] = []
        with self.assertRaises(ValueError):
            analyze_holder_snapshot(data)

    def test_duplicate_accounts_and_invalid_raw_units_are_rejected(self):
        data = fixture()
        data["token_accounts"][1]["token_account"] = data["token_accounts"][0]["token_account"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            analyze_holder_snapshot(data)
        for value in ("2.0", -1, True, None):
            data = fixture()
            data["token_accounts"][0]["amount_raw"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                analyze_holder_snapshot(data)

    def test_local_screen_command_writes_report(self):
        with tempfile.TemporaryDirectory() as directory:
            rc = main(["screen", "--snapshot", str(FIXTURE), "--equity-sol", "10",
                       "--output", directory])
            self.assertEqual(rc, 0)
            report = json.loads((Path(directory) / "report.json").read_text())
            self.assertTrue(report["decision"]["eligible"])
            self.assertEqual(report["mode"], "read_only_holder_screen")
            self.assertTrue((Path(directory) / "report.html").exists())


if __name__ == "__main__":
    unittest.main()
