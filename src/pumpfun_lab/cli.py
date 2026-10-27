"""Commands for offline research and explicitly requested read-only history."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .reports import replay_markdown, write_report

STRATEGIES = ("momentum", "creator_filter", "pre_graduation")


def _nonfinite(value):
    raise ValueError(f"Non-finite JSON number is not permitted: {value}")


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=_nonfinite)


def _load_events(path: Path) -> list[dict]:
    events = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line, parse_constant=_nonfinite)
            except ValueError as error:
                raise ValueError(f"Invalid event on line {line_number}: {error}") from None
            if not isinstance(item, dict):
                raise ValueError(f"Event on line {line_number} must be an object")
            events.append(item)
    if not events:
        raise ValueError("Event file is empty")
    return events


def _snapshot(args):
    from .snapshot import build_snapshot_report, render_snapshot_markdown
    report = build_snapshot_report(args.profile, args.trades, args.created)
    return report, render_snapshot_markdown(report)


def _replays(events_path: Path, config_path: Path, strategy: str, source_label: str):
    from .engine import run_replay
    config = _load_json(config_path)
    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object")
    if not source_label.strip():
        raise ValueError("Source label must be nonempty")
    config["source_label"] = source_label.strip()
    events = _load_events(events_path)
    names = STRATEGIES if strategy == "all" else (strategy,)
    return [run_replay(events, config, name) for name in names]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pump.fun Lab: paper research and read-only wallet analysis")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "demo"):
        command = sub.add_parser(name, help="Reconcile supplied summaries (synthetic by default)" if name == "snapshot" else "Run offline synthetic demonstrations")
        command.add_argument("--profile", type=Path, default=Path("data/fixtures/profile_snapshot.json"))
        command.add_argument("--trades", type=Path, default=Path("data/fixtures/top_trades.csv"))
        command.add_argument("--created", type=Path, default=Path("data/fixtures/created_coins.csv"))
        command.add_argument("--output", type=Path, default=Path(f"output/{name}"))
        if name == "demo":
            command.add_argument("--config", type=Path, default=Path("config/paper.json"))
    command = sub.add_parser("replay", help="Replay normalized, timestamped price observations")
    command.add_argument("--events", type=Path, required=True)
    command.add_argument("--source-label", required=True, help="Provenance, e.g. synthetic fixture or recorder/date")
    command.add_argument("--config", type=Path, default=Path("config/paper.json"))
    command.add_argument("--strategy", choices=(*STRATEGIES, "all"), default="all")
    command.add_argument("--output", type=Path, default=Path("output/replay"))
    command = sub.add_parser("wallet", help="Fetch a bounded read-only Solana history sample")
    command.add_argument("--address", required=True, help="Explicit public address to query; there is no saved default")
    command.add_argument("--max-signatures", type=int, default=100)
    command.add_argument("--max-transactions", type=int, default=20)
    command.add_argument("--output", type=Path, default=Path("output/wallet"))
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "snapshot":
            report, markdown = _snapshot(args)
            title = "Wallet snapshot reconciliation"
        elif args.command == "replay":
            results = _replays(args.events, args.config, args.strategy, args.source_label)
            report = {"schema_version": 1, "mode": "paper", "replays": results}
            markdown, title = replay_markdown(results), "Paper replay comparison"
        elif args.command == "demo":
            snapshot, snapshot_md = _snapshot(args)
            results = _replays(Path("data/fixtures/synthetic_market.jsonl"), args.config,
                               "all", "synthetic engineering fixture (not market history)")
            report = {"schema_version": 1, "mode": "paper", "snapshot": snapshot, "replays": results}
            markdown = snapshot_md + "\n\n---\n\n" + replay_markdown(results)
            title = "Pump.fun Lab — initial research report"
        else:
            from .wallet import fetch_wallet_history
            report = fetch_wallet_history(args.address,
                                          max_signatures=args.max_signatures,
                                          max_transactions=args.max_transactions)
            markdown = "\n".join([
                "# Read-only wallet history sample", "",
                f"Wallet: `{args.address}`", "",
                "This is a limited observation sample, not a complete trade ledger or profitability assessment.", "",
                "## Completeness", "", "```json", json.dumps(report["completeness"], indent=2), "```", "",
                "## Provenance", "", "```json", json.dumps(report["provenance"], indent=2), "```", "",
                "## Retrieval errors", "", "```json", json.dumps(report.get("errors", []), indent=2), "```", "",
                "Raw responses and normalized balance changes are in report.json.", "",
            ])
            title = "Read-only wallet history"
        report["report_generated_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_report(args.output, report, markdown, title)
        print(f"Saved {args.output / 'report.html'}")
        print(f"Saved {args.output / 'report.json'}")
        if args.command == "wallet" and report.get("errors"):
            print("History contains retrieval errors; see completeness and errors in the report.", file=sys.stderr)
            return 1
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
