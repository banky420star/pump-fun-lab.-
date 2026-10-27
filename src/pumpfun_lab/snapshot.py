"""Import and reconcile a supplied profile; never infer an audited ledger.

Money is parsed with Decimal and serialized as strings. The input is a selected
per-coin summary, with no transaction signatures or timestamps, so this module
deliberately returns no wallet-wide win rate or independently measured ROI.
"""

from __future__ import annotations

import csv
import html
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid numeric value for {field}") from exc
    if not result.is_finite():
        raise ValueError(f"Nonfinite numeric value for {field}")
    return result


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _read_csv(path: str | Path, required: set[str]) -> list[dict]:
    with Path(path).open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames or []
        normalized_headers = [name.strip() for name in fieldnames]
        if any(not name for name in normalized_headers) or len(set(normalized_headers)) != len(normalized_headers):
            raise ValueError(f"Empty or duplicate column names in {Path(path).name}")
        if not required.issubset(set(fieldnames)):
            raise ValueError(f"Missing required columns in {Path(path).name}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"Malformed CSV rows in {Path(path).name}")
    return rows


def _displayed_amount(value: str) -> Decimal:
    """Read rounded UI text such as $2.50K; it remains an estimate."""
    text = value.strip().replace("$", "").replace(",", "")
    multiplier = Decimal(1)
    if text[-1:].upper() in {"K", "M", "B", "T"}:
        multiplier = {"K": Decimal(1000), "M": Decimal(10**6),
                      "B": Decimal(10**9), "T": Decimal(10**12)}[text[-1].upper()]
        text = text[:-1]
    amount = _decimal(text, "spent_displayed") * multiplier
    if amount < 0:
        raise ValueError("Displayed spending cannot be negative")
    return amount


def _assert_unique(rows: list[dict], field: str) -> None:
    values = [row[field].strip() for row in rows]
    if any(not value for value in values) or len(set(values)) != len(values):
        raise ValueError(f"Empty or duplicate {field}; cannot safely aggregate")


def build_snapshot_report(
    profile_path: str | Path, trades_path: str | Path, created_path: str | Path,
) -> dict:
    """Return a JSON-safe report, retaining source values and their limits.

    The arithmetic residual is *only* a reconciliation difference. It is not
    labeled losses because equal scope, timing and fee treatment are unverified.
    """
    with Path(profile_path).open(encoding="utf-8") as stream:
        profile = json.load(stream)
    source = profile.get("source", "user_pasted_ui")
    synthetic = source == "synthetic_demo"
    dates_are_fictional = synthetic or profile.get("dates_are_fictional") is True
    for key in ("realized_profit_usd_reported", "unrealized_profit_usd_reported",
                "headline_profit_usd_reported", "portfolio_value_usd_reported",
                "buy_volume_usd_reported", "headline_return_pct_reported"):
        _decimal(profile[key], key)
    trades = _read_csv(trades_path, {"rank", "mint", "label", "profit_usd_reported",
                                    "spent_displayed", "roi_pct_displayed", "date_displayed"})
    created = _read_csv(created_path, {"mint", "name", "ticker", "market_cap_displayed", "age_displayed"})
    _assert_unique(trades, "mint")
    _assert_unique(trades, "rank")
    _assert_unique(created, "mint")
    created_by_mint = {row["mint"]: row for row in created}
    for row in trades:
        try:
            rank = int(row["rank"])
        except ValueError as exc:
            raise ValueError("Rank must be a positive integer") from exc
        if rank < 1:
            raise ValueError("Rank must be a positive integer")
        row["rank"] = rank
        row["profit_usd_reported"] = _money(_decimal(row["profit_usd_reported"], "profit_usd_reported"))
        _decimal(row["roi_pct_displayed"], "roi_pct_displayed")
        row["spent_usd_display_estimate"] = _money(_displayed_amount(row["spent_displayed"]))
        row["spent_is_exact_cost_basis"] = False
        row["creation_classification"] = (
            "matches_user_reported_created_coin" if row["mint"] in created_by_mint else "not_established"
        )
        if "date_year_inferred" in row:
            row["date_year_inferred"] = row["date_year_inferred"].lower() == "true"
    trades.sort(key=lambda row: row["rank"])
    if len({row["rank"] for row in trades}) != len(trades):
        raise ValueError("Duplicate numeric rank; cannot safely rank")
    profits = [_decimal(row["profit_usd_reported"], "profit_usd_reported") for row in trades]
    total = sum(profits, Decimal(0))
    top5 = sum(sorted(profits, reverse=True)[:5], Decimal(0))
    top10 = sum(sorted(profits, reverse=True)[:10], Decimal(0))
    realized = _decimal(profile["realized_profit_usd_reported"], "realized_profit_usd_reported")
    unrealized = _decimal(profile["unrealized_profit_usd_reported"], "unrealized_profit_usd_reported")
    headline = _decimal(profile["headline_profit_usd_reported"], "headline_profit_usd_reported")
    matched = [row for row in trades if row["mint"] in created_by_mint]
    if dates_are_fictional:
        date_limitation = "The supplied dates are fictional demonstration values, not verified market timestamps."
    elif profile.get("observed_at") is None:
        date_limitation = "The actual source capture time is unknown; the session date is provenance, not a verified market timestamp."
    else:
        date_limitation = "The supplied observation time is source metadata, not an independently verified market timestamp."
    if any(row.get("date_year_inferred") for row in trades):
        date_limitation += " One or more trade rows have an inferred year, as marked in their date metadata."
    limitations = [
        "The Top trades list is selected by profit and contains per-coin summaries, not individual executions. It cannot establish an overall win rate, average trade return or drawdown.",
        date_limitation,
        "The profile totals and selected rows have not been reconciled to on-chain transactions. The residual is not a proven loss total or a measured fee total.",
        f"The displayed {profile['headline_return_pct_reported']}% return has no verified denominator in this import. Buy volume is turnover and must not be treated as starting capital.",
        "Displayed spending may be rounded or abbreviated. It is not an exact cost basis and displayed ROI percentages need not reproduce from those rounded amounts.",
        f"Only the {len(created)} supplied created-coin mints can be matched. An unmatched mint is unclassified, not evidence of an external creator. Identical names and tickers can refer to different mints.",
        "Creation costs, creator rewards, deposits, withdrawals, network fees, transfer activity and remaining inventory need a complete ledger before evaluating trading performance.",
        "This dataset contains no price path, order timestamps, executable quotes or rejected transactions; it cannot support a strategy backtest or a claim of reproducible profit.",
    ]
    if synthetic:
        limitations.insert(0, "This synthetic demo uses fabricated identifiers, names, dates and amounts. It does not describe a real wallet or observed trading performance.")
    return {
        "schema_version": 1,
        "provenance": {
            "source": source,
            "source_description": profile.get("source_description"),
            "session_date": profile.get("session_date"),
            "observed_at": profile.get("observed_at"),
            "dates_are_fictional": dates_are_fictional,
            "dataset_kind": "selected_per_coin_summaries",
            "complete_transaction_history": False,
            "independently_verified": False,
        },
        "profile": profile,
        "metrics": {
            "top_list_row_count": len(trades),
            "top_list_unique_mints": len({row["mint"] for row in trades}),
            "top_list_profit_usd": _money(total),
            "top5_profit_usd": _money(top5),
            "top10_profit_usd": _money(top10),
            "top5_share_of_top_list_pct": _money(top5 / total * 100) if total > 0 else None,
            "top10_share_of_top_list_pct": _money(top10 / total * 100) if total > 0 else None,
            "displayed_spend_estimate_usd": _money(sum((_decimal(row["spent_usd_display_estimate"], "spent") for row in trades), Decimal(0))),
            "positive_rows_in_selected_list": sum(value > 0 for value in profits),
            "overall_win_rate_pct": None,
            "true_roi_pct": None,
            "max_drawdown_pct": None,
        },
        "reconciliation": {
            "realized_minus_top_list_usd": _money(realized - total),
            "realized_plus_unrealized_usd": _money(realized + unrealized),
            "headline_minus_components_usd": _money(headline - realized - unrealized),
            "aggregate_and_subset_scope_verified": False,
            "proven_total_losses_usd": None,
            "status": "unreconciled",
        },
        "created_coin_matches": {
            "created_coins_reported": profile.get("coins_created_reported"),
            "visible_created_rows": len(created),
            "matched_top_trade_rows": len(matched),
            "unclassified_top_trade_rows": len(trades) - len(matched),
            "matched_selected_profit_usd": _money(sum((_decimal(row["profit_usd_reported"], "profit") for row in matched), Decimal(0))),
            "matched_mints": [row["mint"] for row in matched],
            "verification": ("synthetic_demo_only" if synthetic else
                             "user_reported_only" if source == "user_pasted_ui" else
                             "source_reported_only"),
        },
        "top_trades": trades,
        "created_coins": created,
        "limitations": limitations,
    }


def _markdown(value: object) -> str:
    """Escape token text so a name cannot turn into HTML or table syntax."""
    result = html.escape(str(value), quote=True).replace("\n", " ").replace("\r", " ")
    for char in ("\\", "`", "*", "_", "[", "]", "|", "~"):
        result = result.replace(char, "\\" + char)
    return result


def render_snapshot_markdown(report: dict) -> str:
    """Render source limits alongside the findings and all supplied rows."""
    p, m, r, c = (report[key] for key in ("profile", "metrics", "reconciliation", "created_coin_matches"))
    provenance = report["provenance"]
    source = provenance["source"]
    synthetic = source == "synthetic_demo"
    source_label = {"synthetic_demo": "synthetic demo (fabricated data)",
                    "user_pasted_ui": "user-pasted Pump.fun UI"}.get(source, source)
    source_text = f"Source: {_markdown(source_label)}."
    if provenance.get("source_description"):
        source_text += f" {_markdown(provenance['source_description'])}"
    if provenance.get("session_date"):
        session_label = "Fictional session date" if provenance.get("dates_are_fictional") else "Session date"
        source_text += f" {session_label}: {_markdown(provenance['session_date'])}."
    if provenance.get("observed_at"):
        source_text += f" Supplied observation time: {_markdown(provenance['observed_at'])}."
    elif synthetic:
        source_text += " No real observation time applies."
    else:
        source_text += " Actual capture time is unknown."
    source_text += " No figures have been independently verified."
    if provenance.get("dates_are_fictional"):
        date_note = "Dates are fictional demonstration values."
    elif any(row.get("date_year_inferred") for row in report["top_trades"]):
        date_note = "One or more dates include an inferred year, as marked in the row metadata."
    else:
        date_note = "Dates are retained as supplied; their accuracy is unverified."
    lines = [
        f"# {'Synthetic ' if synthetic else ''}Pump.fun profile snapshot: {_markdown(p['username'])}", "",
        f"Wallet: `{p['wallet_address']}`", "",
        source_text, "",
        "## Findings", "",
        "| Measure | Value |", "|---|---:|",
        f"| Reported realized profit | ${p['realized_profit_usd_reported']} |",
        f"| Reported unrealized profit | ${p['unrealized_profit_usd_reported']} |",
        f"| Reported headline profit | ${p['headline_profit_usd_reported']} |",
        f"| Reported buy volume | ${p['buy_volume_usd_reported']} |",
        f"| Reported portfolio value | ${p['portfolio_value_usd_reported']} |",
        f"| Selected Top trades rows | {m['top_list_row_count']} |",
        f"| Sum of selected reported profits | ${m['top_list_profit_usd']} |",
        f"| Top five share of selected profit | {m['top5_share_of_top_list_pct']}% |",
        f"| Top ten share of selected profit | {m['top10_share_of_top_list_pct']}% |",
        f"| Realized total minus selected profit sum | ${r['realized_minus_top_list_usd']} |", "",
        "The last line is an arithmetic reconciliation difference, not a proven total of losses. Equal time period, fee treatment and inclusion rules have not been verified.", "",
        f"Realized plus unrealized equals ${r['realized_plus_unrealized_usd']}; the headline is ${r['headline_minus_components_usd']} higher. Retain this discrepancy until the underlying records explain it.", "",
        "## Creator cohort", "",
        f"The profile reports {c['created_coins_reported']} created coins and supplies {c['visible_created_rows']} creation rows. {c['matched_top_trade_rows']} Top trades mints match those rows; the remaining {c['unclassified_top_trade_rows']} have unestablished creator status. Matching is by mint identifier, never ticker.", "",
        "Future testing should keep trades involving the user's own creations separate from entries into independently created tokens, with creation costs and creator rewards separately accounted for.", "",
        "## Evidence limits", "",
    ]
    lines.extend(f"- {_markdown(item)}" for item in report["limitations"])
    lines.extend(["", "## All supplied Top trades", "",
                  f"Spent values and ROI are displayed figures. {date_note}", "",
                  "| Rank | Token | Mint | Reported profit USD | Displayed spent | Displayed ROI | Date | Creation match |",
                  "|---:|---|---|---:|---:|---:|---|---|"])
    for row in report["top_trades"]:
        match = ("Synthetic creation list" if synthetic else "Supplied creation list") if row["creation_classification"] == "matches_user_reported_created_coin" else "Unclassified"
        lines.append(f"| {row['rank']} | {_markdown(row['label'])} | {_markdown(row['mint'])} | {row['profit_usd_reported']} | {_markdown(row['spent_displayed'])} | {_markdown(row['roi_pct_displayed'])}% | {_markdown(row['date_displayed'])} | {match} |")
    return "\n".join(lines) + "\n"
