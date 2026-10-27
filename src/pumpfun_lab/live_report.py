"""Report a real collection separately from hypothetical execution results."""

from datetime import datetime
import json

from .engine import run_replay
from .reports import replay_markdown


def build_live_report(capture: dict, config: dict, strategy: str = "momentum") -> tuple[dict, str]:
    observations = capture["observations"]
    results = []
    errors = []
    actual_transport = capture.get("transport") == "websockets"
    last_observation = observations[-1]["ts_ms"] if observations else None
    ended_ms = round(datetime.fromisoformat(capture["ended_at_utc"]).timestamp() * 1000)
    trailing_gap = max(0, ended_ms - last_observation) if last_observation is not None else None
    provenance = ("PumpPortal live reserve-ratio indications" if actual_transport
                  else "Injected or unverified capture; not live market evidence")
    if observations:
        replay_config = dict(config)
        replay_config["source_label"] = (
            f"{provenance}, received "
            f"{capture['started_at_utc']} to {capture['ended_at_utc']}; "
            "unfinalized, bounded sample; hypothetical execution"
        )
        try:
            results = [run_replay(observations, replay_config, strategy)]
        except (ValueError, OverflowError):
            # Keep the paid/real capture even if numeric extremes defeat the model.
            errors.append({"category": "paper_replay_failed",
                           "message": "Observed data could not be replayed; the capture is preserved"})
    report = {
        "schema_version": 1,
        "mode": "paper",
        "capture": capture,
        "replays": results,
        "evaluation": {
            "strategy_tested": bool(results),
            "live_market_evidence": actual_transport and capture["market_data_received"],
            "errors": errors,
            "profitability_established": False,
            "executable_quotes_verified": False,
            "actual_data_cost_sol": None,
            "data_and_hosting_costs_in_paper_pnl": False,
            "paper_valuation_ts_ms": last_observation if results else None,
            "capture_ended_ts_ms": ended_ms,
            "unobserved_tail_ms": trailing_gap,
            "notes": [
                "Discovery events do not establish profitable entry or exit behavior.",
                "Replay runs after collection; this is not a continuous order executor.",
                "Reserve ratios are indicative prices, not executable buy/sell quotes.",
                "Missing liquidity and sellability use the paper engine's documented assumptions.",
                "The first subscribed launches form a bounded sample, not the whole market.",
                "Reported paper PnL excludes actual data and hosting charges, which are unknown.",
                "Message limits are local stop conditions, not a guaranteed provider billing cap.",
                "Creator history and graduation progress are not supplied by this adapter.",
                "Paper inventory is valued at the last accepted observation, not at the later capture end; the unobserved tail remains unknown.",
            ],
        },
    }
    counts = capture["counts"]
    lines = [
        "# Live Pump.fun market sample" if actual_transport else "# Injected or unverified market sample", "",
        "This collector submits no orders. Transport provenance is retained below.", "",
        "## Connection evidence", "",
        f"- Capture mode: {capture['mode']}.",
        f"- Transport: {capture.get('transport') or 'unknown'}.",
        f"- WebSocket connected: {capture['connected']}.",
        f"- Market events received: {capture['market_data_received']}.",
        f"- Started (UTC): {capture['started_at_utc']}.",
        f"- Ended (UTC): {capture['ended_at_utc']}.",
        f"- Stop reason: {capture['end_reason']}.",
        f"- Messages processed: {counts.get('messages', 0)}.",
        f"- New-token events: {counts.get('new_token', 0)}.",
        f"- Migration events: {counts.get('migration', 0)}.",
        f"- Price observations accepted: {len(observations)}.", "",
        "## What this establishes", "",
    ]
    if not actual_transport:
        lines.append("This is injected or unverified input, not evidence of a real market connection.")
    elif not capture["market_data_received"]:
        lines.append("No live market-event evidence was obtained in this session.")
    elif errors:
        lines.append("Live data arrived, but the paper model could not evaluate it. The actual capture is retained and no PnL is reported.")
    elif not observations:
        lines.append("Live discovery data arrived. No trade-price sequence was available, so no strategy was tested and no PnL is reported.")
    else:
        lines.append("Real feed observations were collected and replayed through a hypothetical execution model. No real trades were made and profitable live execution has not been established.")
    if trailing_gap is not None:
        lines += ["", f"The last accepted price preceded capture end by {trailing_gap / 1000:.3f} seconds. "
                  "Paper inventory is valued at that last observation, not at capture end."]
    lines += ["", "## Evidence limits", ""]
    lines.extend(f"- {note}" for note in report["evaluation"]["notes"])
    lines += ["", "## Capture errors", "", "```json", json.dumps(capture["errors"], indent=2),
              "```", "", "## Paper evaluation errors", "", "```json", json.dumps(errors, indent=2), "```", "",
              "Sanitized events and completeness are in report.json. Normalized observations are also in events.jsonl.", ""]
    if results:
        lines += ["---", "", replay_markdown(results)]
    return report, "\n".join(lines)
