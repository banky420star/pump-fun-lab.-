import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def invoke(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        return subprocess.run(
            [sys.executable, "-m", "pumpfun_lab", *args],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=20,
        )

    def test_help_exposes_research_commands(self):
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("demo", "snapshot", "replay", "wallet"):
            self.assertIn(command, result.stdout)

    def test_malformed_replay_file_fails_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = Path(tmp) / "bad.jsonl"
            events.write_text('{"ts_ms": NaN}\n', encoding="utf-8")
            result = self.invoke("replay", "--events", str(events),
                                 "--source-label", "synthetic test",
                                 "--output", str(Path(tmp) / "report"))
            self.assertEqual(result.returncode, 2)
            self.assertIn("error:", result.stderr.lower())
            self.assertNotIn("Traceback", result.stderr)

    def test_replay_requires_explicit_provenance(self):
        result = self.invoke("replay", "--events", "missing.jsonl")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--source-label", result.stderr)

    def test_wallet_requires_explicit_address(self):
        from pumpfun_lab.cli import parser
        error = StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as caught:
            parser().parse_args(["wallet"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("--address", error.getvalue())

    def test_demo_generates_three_labeled_replays_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.invoke("demo", "--output", tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((Path(tmp) / "report.json").read_text())
            self.assertEqual(len(report["replays"]), 3)
            self.assertEqual({x["strategy"] for x in report["replays"]},
                             {"momentum", "creator_filter", "pre_graduation"})
            self.assertTrue(all("synthetic" in x["source_label"].lower()
                                for x in report["replays"]))
            self.assertEqual(len(report["snapshot"]["top_trades"]), 50)
            self.assertEqual(report["snapshot"]["provenance"]["source"], "synthetic_demo")
            self.assertTrue((Path(tmp) / "report.html").is_file())
            self.assertIn("profitability", (Path(tmp) / "report.md").read_text().lower())

    def test_html_renders_untrusted_names_as_text(self):
        from pumpfun_lab.reports import render_html
        html = render_html("<script>bad()</script>", "<img src=x onerror=bad()>")
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)

    def test_unknown_config_key_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text('{"not_a_setting": 20}')
            events = Path(tmp) / "event.jsonl"
            events.write_text('{"ts_ms": 0, "mint": "A", "price_sol": 1}\n')
            result = self.invoke("replay", "--events", str(events),
                                 "--config", str(config), "--source-label", "synthetic",
                                 "--output", str(Path(tmp) / "report"))
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
