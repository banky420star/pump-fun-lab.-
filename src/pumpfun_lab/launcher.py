"""Interactive, finite local market samples; paper simulation only."""

from contextlib import contextmanager
from datetime import datetime, timezone
import getpass
import os
from pathlib import Path
import sys
from uuid import uuid4
import warnings
import webbrowser

from . import cli


OUTPUT_ROOT = Path("output/local")
_KEY_ENV = "PUMPPORTAL_API_KEY"


class _InputError(Exception):
    """A locally authored validation message that is safe to display."""


@contextmanager
def _session_key(secret):
    """Expose the selected key only at the in-process CLI boundary."""
    previous = os.environ.pop(_KEY_ENV, None)
    try:
        if secret is not None:
            os.environ[_KEY_ENV] = secret
        yield
    finally:
        os.environ.pop(_KEY_ENV, None)
        if previous is not None:
            os.environ[_KEY_ENV] = previous


def _duration(default):
    answer = input(f"Session duration in seconds, 1–900 [{default}]: ").strip()
    try:
        duration = int(answer) if answer else default
    except ValueError:
        duration = 0
    if not 1 <= duration <= 900:
        raise _InputError("Session duration must be an integer from 1 to 900 seconds.")
    return duration


def _paid_key():
    print("\nPumpPortal advertises 0.01 SOL per 10,000 trade-data messages.")
    print("The provider requires at least 0.02 SOL in the API key's linked wallet.")
    print("The API key can also authorize real trades at the provider; treat it as a secret.")
    print("Use your chosen wallet's API key. Never enter a wallet private key or recovery words.")
    print("The local message limit is NOT a guaranteed provider billing cap.")
    if input("Accept metered data charges for THIS session? Type exactly YES: ") != "YES":
        print("Cancelled; no metered session started.")
        return None
    with warnings.catch_warnings():
        # getpass warns before its echoed-input fallback. Raising stops that path.
        warnings.simplefilter("error", getpass.GetPassWarning)
        secret = getpass.getpass("PumpPortal API key (hidden, for this session only): ").strip()
    if not secret or len(secret) > 4096 or "\x00" in secret:
        raise _InputError("A nonempty, valid API key is required; no session started.")
    return secret


def _show_report(output):
    report = output / "report.html"
    print(f"\nSession output: {output.resolve()}")
    if not report.is_file():
        print("No HTML report was saved for this session.")
        return
    print(f"Result: {report.resolve()}")
    try:
        # A browser subprocess must not inherit either the new or a preexisting key.
        with _session_key(None):
            opened = webbrowser.open(report.resolve().as_uri())
        if not opened:
            print("Open the result file in your browser to read the report.")
    except (Exception, KeyboardInterrupt):
        print("Could not open a browser; open the result file above manually.")


def main():
    output = None
    try:
        print("\nPump.fun Lab — PAPER ONLY; no orders or transactions are submitted.")
        print("One finite session per launch. Keep your Mac awake until it finishes.")
        print("Each session starts a new virtual account; holdings do not carry over.")
        print("1  Free discovery check (default 90 seconds; no strategy price test)")
        print("2  Metered live observations + paper replay (default 300 seconds)")
        print("q  Quit")
        choice = input("Choose [1]: ").strip().lower() or "1"
        if choice == "q":
            return 0
        if choice not in ("1", "2"):
            raise _InputError("Choose 1, 2, or q.")
        mode = "discovery" if choice == "1" else "trades"
        duration = _duration(90 if mode == "discovery" else 300)
        print(f"\nCollection limit: {duration} seconds or 1,000 locally processed messages.")
        print("Trade mode follows at most the first 10 newly observed tokens.")
        print("Collection can end early. Paper replay runs afterward; this is not continuous.")
        print("Press Ctrl+C to stop. A report is saved when partial capture can be retained.")
        secret = _paid_key() if mode == "trades" else None
        if mode == "trades" and secret is None:
            return 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        output = OUTPUT_ROOT / f"{stamp}-{uuid4().hex[:8]}"
        output.mkdir(parents=True, exist_ok=False)
        argv = ["live", "--mode", mode, "--duration-seconds", str(duration),
                "--max-messages", "1000", "--max-tokens", "10", "--output", str(output)]
        if mode == "trades":
            argv.append("--accept-data-charges")
        with _session_key(secret):
            return cli.main(argv)
    except getpass.GetPassWarning:
        print("Hidden input is unavailable. Run this starter in Terminal; no session started.", file=sys.stderr)
        return 2
    except _InputError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except EOFError:
        print("\nCancelled.")
        return 0
    except KeyboardInterrupt:
        print("\nStopped. Any completed report is listed below.")
        return 130
    except Exception:
        # Provider/transport exceptions may contain credential-bearing URLs.
        print("Session failed; error details omitted to protect credentials.", file=sys.stderr)
        return 2
    finally:
        if output is not None:
            _show_report(output)


if __name__ == "__main__":
    raise SystemExit(main())
