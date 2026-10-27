"""Offline checks for consent, local credential handling, and finite sessions."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import getpass
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

from pumpfun_lab import cli, launcher


SECRET = "TEST_ONLY_ENTERED_KEY"
PREVIOUS = "TEST_ONLY_PREVIOUS_KEY"
CLI_MAIN = cli.main


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.launcher = launcher
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.output_root = Path(directory) / "output" / "local"
        self.stack.enter_context(patch.object(self.launcher, "OUTPUT_ROOT", self.output_root))
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.browser = self.stack.enter_context(patch.object(self.launcher.webbrowser, "open", return_value=True))

    def launch(self, answers, *, key_effect=None, cli_effect=None):
        out, err = StringIO(), StringIO()
        with patch("builtins.input", side_effect=answers), \
                patch.object(getpass, "getpass", return_value=SECRET, side_effect=key_effect) as reader, \
                patch.object(cli, "main", return_value=0, side_effect=cli_effect) as runner, \
                redirect_stdout(out), redirect_stderr(err):
            status = self.launcher.main()
        return status, runner, reader, out.getvalue() + err.getvalue()

    def save_report(self, argv, contents="<html>paper test</html>"):
        args = cli.parser().parse_args(argv)
        (args.output / "report.html").write_text(contents, encoding="utf-8")
        return 0

    def test_charge_decline_never_reads_a_key_or_starts_a_session(self):
        for reply in ("", "no", "yes", "Yes", "YES ", " YES"):
            with self.subTest(reply=reply):
                status, runner, reader, _ = self.launch(["2", "", reply])
                self.assertEqual(status, 0)
                runner.assert_not_called()
                reader.assert_not_called()
                self.assertFalse(self.output_root.exists())

    def test_free_default_suppresses_inherited_key_and_uses_finite_limits(self):
        os.environ["PUMPPORTAL_API_KEY"] = PREVIOUS

        def run(argv):
            self.assertNotIn("PUMPPORTAL_API_KEY", os.environ)
            args = cli.parser().parse_args(argv)
            self.assertEqual((args.mode, args.duration_seconds, args.max_messages, args.max_tokens),
                             ("discovery", 90, 1000, 10))
            self.assertFalse(args.accept_data_charges)
            return 1

        status, _, reader, text = self.launch(["", ""], cli_effect=run)
        self.assertEqual(status, 1)
        reader.assert_not_called()
        self.assertEqual(os.environ["PUMPPORTAL_API_KEY"], PREVIOUS)
        self.assertNotIn(PREVIOUS, text)
        self.browser.assert_not_called()

    def test_trade_key_is_available_only_during_cli_and_not_in_saved_outputs_or_args(self):
        from test_live_cli import capture_fixture

        def capture(**kwargs):
            self.assertEqual(os.environ["PUMPPORTAL_API_KEY"], SECRET)
            self.assertEqual(kwargs["api_key"], SECRET)
            self.assertTrue(kwargs["accept_data_charges"])
            self.assertEqual((kwargs["duration_seconds"], kwargs["max_messages"], kwargs["max_tokens"]),
                             (300, 1000, 10))
            return capture_fixture("trades")

        def open_report(uri):
            self.assertNotIn("PUMPPORTAL_API_KEY", os.environ)
            self.assertTrue(uri.startswith("file:"))
            return True

        self.browser.side_effect = open_report
        with patch("pumpfun_lab.live.capture_market", side_effect=capture):
            status, runner, reader, text = self.launch(["2", "", "YES"], cli_effect=CLI_MAIN)
        self.assertEqual(status, 0)
        reader.assert_called_once()
        self.assertNotIn("PUMPPORTAL_API_KEY", os.environ)
        self.assertNotIn(SECRET, repr(runner.call_args))
        self.assertNotIn(SECRET, text)
        reports = list(self.output_root.rglob("report.html"))
        self.assertEqual(len(reports), 1)
        for path in self.output_root.rglob("*"):
            if path.is_file():
                self.assertNotIn(SECRET, path.read_text(encoding="utf-8"))
        self.browser.assert_called_once_with(reports[0].resolve().as_uri())

    def test_cli_failure_restores_prior_key_without_printing_exception_details(self):
        for previous in (None, PREVIOUS):
            for error_type in (RuntimeError, ValueError, OSError):
                with self.subTest(previous=previous, error_type=error_type):
                    if previous is not None:
                        os.environ["PUMPPORTAL_API_KEY"] = previous

                    def fail(argv):
                        self.assertEqual(os.environ["PUMPPORTAL_API_KEY"], SECRET)
                        raise error_type("A credential-bearing error: " + SECRET)

                    status, runner, _, text = self.launch(["2", "", "YES"], cli_effect=fail)
                    self.assertEqual(status, 2)
                    self.assertEqual(os.environ.get("PUMPPORTAL_API_KEY"), previous)
                    self.assertNotIn(SECRET, repr(runner.call_args))
                    self.assertNotIn(SECRET, text)
                    self.assertNotIn(PREVIOUS, text)

    def test_getpass_warning_stops_before_echoed_fallback(self):
        echoed_fallback = []

        def insecure_prompt(prompt):
            warnings.warn("Cannot disable echo", getpass.GetPassWarning)
            echoed_fallback.append(True)
            return SECRET

        status, runner, _, text = self.launch(["2", "", "YES"], key_effect=insecure_prompt)
        self.assertEqual(status, 2)
        self.assertEqual(echoed_fallback, [])
        runner.assert_not_called()
        self.assertFalse(self.output_root.exists())
        self.assertNotIn(SECRET, text)

    def test_invalid_duration_stops_before_secret_entry_or_collection(self):
        for duration in ("0", "901", "-1", "1.5", "nan", "forever"):
            with self.subTest(duration=duration):
                status, runner, reader, _ = self.launch(["2", duration])
                self.assertEqual(status, 2)
                runner.assert_not_called()
                reader.assert_not_called()
                self.assertFalse(self.output_root.exists())

    def test_duration_boundaries_are_forwarded_to_collector(self):
        for duration in (1, 900):
            with self.subTest(duration=duration):
                status, runner, _, _ = self.launch(["1", str(duration)])
                self.assertEqual(status, 0)
                self.assertEqual(cli.parser().parse_args(runner.call_args.args[0]).duration_seconds,
                                 duration)

    def test_new_sessions_keep_separate_report_directories(self):
        paths = []
        for _ in range(2):
            status, runner, _, _ = self.launch(["1", ""], cli_effect=self.save_report)
            self.assertEqual(status, 0)
            paths.append(cli.parser().parse_args(runner.call_args.args[0]).output)
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual(len(list(self.output_root.iterdir())), 2)
        for path in paths:
            self.assertEqual((path / "report.html").read_text(), "<html>paper test</html>")

    def test_interrupt_cleans_up_key_and_returns_interrupted_exit_code(self):
        os.environ["PUMPPORTAL_API_KEY"] = PREVIOUS
        status, _, _, text = self.launch(["2", "", "YES"], cli_effect=KeyboardInterrupt)
        self.assertEqual(status, 130)
        self.assertEqual(os.environ["PUMPPORTAL_API_KEY"], PREVIOUS)
        self.assertNotIn(SECRET, text)

    def test_report_open_failure_preserves_cli_exit_code(self):
        self.browser.side_effect = OSError("browser unavailable")

        def run(argv):
            self.save_report(argv)
            return 1

        status, _, _, _ = self.launch(["1", ""], cli_effect=run)
        self.assertEqual(status, 1)

    def test_empty_key_is_rejected_without_starting_collection(self):
        status, runner, _, _ = self.launch(["2", "", "YES"], key_effect=lambda _: "  ")
        self.assertEqual(status, 2)
        runner.assert_not_called()

    def test_quit_eof_and_invalid_menu_never_start_collection(self):
        for answers, expected in ((["q"], 0), ([EOFError], 0), (["x"], 2)):
            with self.subTest(answers=answers):
                status, runner, reader, _ = self.launch(answers)
                self.assertEqual(status, expected)
                runner.assert_not_called()
                reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
