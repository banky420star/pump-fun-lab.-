"""Causal, long-only paper replay using supplied price observations.

This is an accounting and experiment harness, not a Pump.fun execution client.
Prices, liquidity and sellability are assertions supplied by the dataset.  No
wallet, order-routing, chain state, curve pricing or RPC access is implemented.
"""

from collections import defaultdict, deque
import math


STRATEGIES = ("momentum", "creator_filter", "pre_graduation")

DEFAULT_CONFIG = {
    "initial_balance_sol": 10.0,
    "position_sol": 0.05,
    "max_positions": 3,
    "protocol_fee_bps": 125.0,
    "slippage_bps": 100.0,
    "network_fee_sol": 0.00001,
    "latency_ms": 1000,
    "max_quote_age_ms": 5000,
    "stop_loss_bps": 1500.0,
    "take_profit_bps": 2500.0,
    "max_hold_ms": 60000,
    "momentum_min_return_bps": 500.0,
    "creator_min_prior_launches": 1,
    "creator_min_success_rate": 0.5,
    "pre_graduation_min_progress": 0.8,
    "pre_graduation_max_progress": 0.95,
    "source_label": "user data",
}


def _number(value, label, *, minimum=0, maximum=None, strict_minimum=False):
    """Validate JSON numeric input without accepting booleans or nonfinite data."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    if number < minimum or (strict_minimum and number == minimum):
        raise ValueError(f"{label} is below its permitted minimum")
    if maximum is not None and number > maximum:
        raise ValueError(f"{label} exceeds its permitted maximum")
    return number


def _integer(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _configuration(config):
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")
    unknown = set(config) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError("unknown config keys: " + ", ".join(sorted(map(str, unknown))))
    result = {**DEFAULT_CONFIG, **config}
    for key in ("initial_balance_sol", "network_fee_sol", "take_profit_bps", "momentum_min_return_bps"):
        result[key] = _number(result[key], key)
    result["position_sol"] = _number(result["position_sol"], "position_sol", strict_minimum=True)
    for key in ("protocol_fee_bps", "slippage_bps"):
        result[key] = _number(result[key], key, maximum=10000)
        if result[key] == 10000:
            raise ValueError(f"{key} must be < 10000")
    result["stop_loss_bps"] = _number(result["stop_loss_bps"], "stop_loss_bps", maximum=10000)
    for key in ("creator_min_success_rate", "pre_graduation_min_progress", "pre_graduation_max_progress"):
        result[key] = _number(result[key], key, maximum=1)
    for key in ("max_positions", "max_quote_age_ms", "max_hold_ms"):
        result[key] = _integer(result[key], key, 1)
    for key in ("latency_ms", "creator_min_prior_launches"):
        result[key] = _integer(result[key], key)
    if result["position_sol"] <= result["network_fee_sol"]:
        raise ValueError("position_sol must exceed network_fee_sol")
    if result["pre_graduation_min_progress"] > result["pre_graduation_max_progress"]:
        raise ValueError("pre_graduation_min_progress must not exceed its maximum")
    if not isinstance(result["source_label"], str) or not result["source_label"].strip():
        raise ValueError("source_label must be a nonempty provenance label")
    return result


def _observations(events):
    if not isinstance(events, list):
        raise ValueError("events must be a list of dictionaries")
    result = []
    previous_ts = -1
    for index, source in enumerate(events):
        label = f"event[{index}]"
        if not isinstance(source, dict):
            raise ValueError(f"{label} must be a dictionary")
        if not all(key in source for key in ("ts_ms", "mint", "price_sol")):
            raise ValueError(f"{label} requires ts_ms, mint and price_sol")
        event = dict(source)
        ts_ms = _integer(event["ts_ms"], f"{label}.ts_ms")
        if ts_ms < previous_ts:
            raise ValueError(f"{label} is out of timestamp order; input is not silently sorted")
        previous_ts = ts_ms
        if not isinstance(event["mint"], str) or not event["mint"].strip():
            raise ValueError(f"{label}.mint must be a nonempty string")
        event["price_sol"] = _number(event["price_sol"], f"{label}.price_sol", strict_minimum=True)
        if "liquidity_sol" in event:
            event["liquidity_sol"] = _number(event["liquidity_sol"], f"{label}.liquidity_sol", strict_minimum=True)
        if "creator_prior_launches" in event:
            _integer(event["creator_prior_launches"], f"{label}.creator_prior_launches")
        for key in ("creator_prior_success_rate", "progress"):
            if key in event:
                event[key] = _number(event[key], f"{label}.{key}", maximum=1)
        if "sellable" in event and not isinstance(event["sellable"], bool):
            raise ValueError(f"{label}.sellable must be boolean")
        event.setdefault("sellable", True)
        result.append(event)
    return result


def _finite(value):
    if not math.isfinite(value):
        raise ValueError("input magnitudes overflow the paper accounting model")
    return value


def run_replay(events: list[dict], config: dict, strategy: str) -> dict:
    """Replay observations and return a JSON-serializable audit report.

    A momentum signal needs three distinct-time observations in the quote-age
    window, two strictly positive consecutive moves, and the configured total
    return. Creator and pre-graduation variants gate that signal with fields
    supplied on its current event; history is never inferred from future data.

    Entry budgets include the buy protocol fee and network fee. Orders execute
    only on a later event for their mint after latency. Entry signals expire;
    exit intents remain pending until an executable later quote arrives. No
    end-of-file liquidation is invented. Marked equity includes an estimated
    exit fee; booked costs include only the sides actually filled.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {', '.join(STRATEGIES)}")
    cfg = _configuration(config)
    observations = _observations(events)
    fee_rate = cfg["protocol_fee_bps"] / 10000
    slip_rate = cfg["slippage_bps"] / 10000
    network = cfg["network_fee_sol"]
    budget = cfg["position_sol"]
    cash = cfg["initial_balance_sol"]
    positions = {}
    orders = {}
    latest = {}
    histories = defaultdict(lambda: deque(maxlen=3))
    fills, trades, rejections = [], [], []
    next_position_id = 1
    peak = cash
    max_drawdown = 0.0
    equity_curve = [{
        "ts_ms": observations[0]["ts_ms"] if observations else None,
        "event_index": -1, "cash_sol": cash, "inventory_value_sol": 0.0,
        "equity_sol": cash, "drawdown_pct": 0.0,
    }]

    def reject(index, now, mint, side, reason, order=None):
        rejections.append({
            "event_index": index, "ts_ms": now, "mint": mint, "side": side,
            "reason": reason,
            "signal_ts_ms": order["signal_ts_ms"] if order else now,
        })

    def mark(position, now):
        quote = latest[position["mint"]]
        age = now - quote["ts_ms"]
        if age > cfg["max_quote_age_ms"]:
            return 0.0, "stale"
        if not quote["sellable"]:
            return 0.0, "unsellable"
        gross = _finite(position["quantity_tokens"] * quote["price_sol"] * (1 - slip_rate))
        if "liquidity_sol" in quote and gross > quote["liquidity_sol"]:
            return 0.0, "insufficient_liquidity"
        net = _finite(gross * (1 - fee_rate) - network)
        return (net, "executable") if net > 0 else (0.0, "uneconomic")

    def new_order(side, mint, now, index, reason):
        order = {
            "side": side, "mint": mint, "signal_ts_ms": now,
            "signal_event_index": index,
            "eligible_after_ts_ms": now + cfg["latency_ms"],
            "signal_reason": reason,
        }
        if side == "buy":
            order["budget_sol"] = budget
            order["expires_after_ts_ms"] = now + cfg["max_quote_age_ms"]
        else:
            order["position_id"] = positions[mint]["position_id"]
        return order

    for index, event in enumerate(observations):
        now, mint = event["ts_ms"], event["mint"]
        latest[mint] = event
        histories[mint].append(event)
        acted = set()

        # The clock may advance on a different mint. Expired entry budgets are
        # released before any new signal is considered at this timestamp.
        for order_mint, order in list(orders.items()):
            if order["side"] == "buy" and now > order["expires_after_ts_ms"]:
                reject(index, now, order_mint, "buy", "stale_buy_signal", order)
                del orders[order_mint]
                acted.add(order_mint)

        order = orders.get(mint)
        if order and index > order["signal_event_index"] and now >= order["eligible_after_ts_ms"]:
            side = order["side"]
            acted.add(mint)
            execution_price = _finite(event["price_sol"] * (1 + slip_rate if side == "buy" else 1 - slip_rate))
            reason = None
            if not event["sellable"]:
                reason = "unsellable"
            elif side == "buy":
                gross = (budget - network) / (1 + fee_rate)
                quantity = _finite(gross / execution_price)
                if cash + budget * 1e-12 < budget:
                    reason = "insufficient_balance"
                elif len(positions) >= cfg["max_positions"]:
                    reason = "max_positions"
                elif "liquidity_sol" in event and gross > event["liquidity_sol"]:
                    reason = "insufficient_liquidity"
            else:
                quantity = positions[mint]["quantity_tokens"]
                gross = _finite(quantity * execution_price)
                if "liquidity_sol" in event and gross > event["liquidity_sol"]:
                    reason = "insufficient_liquidity"
                elif gross * (1 - fee_rate) - network <= 0:
                    reason = "uneconomic_exit"
            if reason:
                reject(index, now, mint, side, reason, order)
                if side == "buy":
                    del orders[mint]
            else:
                protocol_fee = gross * fee_rate
                cashflow = -budget if side == "buy" else gross - protocol_fee - network
                if side == "buy":
                    position_id = f"position-{next_position_id}"
                    next_position_id += 1
                    positions[mint] = {
                        "position_id": position_id, "mint": mint,
                        "quantity_tokens": quantity, "opened_ts_ms": now,
                        "opened_event_index": index, "entry_price_sol": execution_price,
                        "cash_invested_sol": budget,
                    }
                else:
                    position = positions.pop(mint)
                    position_id = position["position_id"]
                    pnl = cashflow - position["cash_invested_sol"]
                    trades.append({
                        **position, "closed_ts_ms": now, "closed_event_index": index,
                        "exit_price_sol": execution_price, "cash_returned_sol": cashflow,
                        "pnl_sol": pnl, "return_pct": _finite(pnl / position["cash_invested_sol"] * 100),
                        "hold_ms": now - position["opened_ts_ms"],
                        "exit_reason": order["signal_reason"],
                    })
                cash = _finite(cash + cashflow)
                if -budget * 1e-12 < cash < 0:
                    cash = 0.0
                fills.append({
                    "position_id": position_id, "side": side, "mint": mint,
                    "signal_ts_ms": order["signal_ts_ms"],
                    "signal_event_index": order["signal_event_index"],
                    "fill_ts_ms": now, "event_index": index,
                    "signal_reason": order["signal_reason"],
                    "observed_price_sol": event["price_sol"],
                    "execution_price_sol": execution_price, "quantity_tokens": quantity,
                    "gross_sol": gross, "protocol_fee_sol": protocol_fee,
                    "network_fee_sol": network, "net_cashflow_sol": cashflow,
                    "modeled_slippage_sol": _finite(quantity * abs(execution_price - event["price_sol"])),
                })
                del orders[mint]

        # Exit signals never fill on their triggering event, including an
        # unsellable observation. Time stops also observe the global clock.
        for position_mint, position in positions.items():
            if position_mint in orders or position["opened_event_index"] == index:
                continue
            reason = None
            if now - position["opened_ts_ms"] >= cfg["max_hold_ms"]:
                reason = "max_hold"
            elif position_mint == mint:
                value, status = mark(position, now)
                if status != "executable":
                    reason = "unexecutable_quote"
                elif value <= position["cash_invested_sol"] * (1 - cfg["stop_loss_bps"] / 10000):
                    reason = "stop_loss"
                elif value >= position["cash_invested_sol"] * (1 + cfg["take_profit_bps"] / 10000):
                    reason = "take_profit"
            if reason:
                orders[position_mint] = new_order("sell", position_mint, now, index, reason)

        history = histories[mint]
        if mint not in acted and mint not in positions and mint not in orders and len(history) == 3:
            a, b, c = history
            momentum = (
                a["ts_ms"] < b["ts_ms"] < c["ts_ms"]
                and now - a["ts_ms"] <= cfg["max_quote_age_ms"]
                and a["price_sol"] < b["price_sol"] < c["price_sol"]
                and _finite((c["price_sol"] / a["price_sol"] - 1) * 10000) >= cfg["momentum_min_return_bps"]
            )
            if momentum:
                reason = None
                if not event["sellable"]:
                    reason = "unsellable"
                elif strategy == "creator_filter" and (
                    event.get("creator_prior_launches", -1) < cfg["creator_min_prior_launches"]
                    or event.get("creator_prior_success_rate", -1) < cfg["creator_min_success_rate"]
                ):
                    reason = "creator_history_gate"
                elif strategy == "pre_graduation" and not (
                    cfg["pre_graduation_min_progress"] <= event.get("progress", -1) <= cfg["pre_graduation_max_progress"]
                ):
                    reason = "progress_gate"
                pending_buys = [o for o in orders.values() if o["side"] == "buy"]
                available = cash - math.fsum(o["budget_sol"] for o in pending_buys)
                if reason is None and len(positions) + len(pending_buys) >= cfg["max_positions"]:
                    reason = "max_positions"
                elif reason is None and available + budget * 1e-12 < budget:
                    reason = "insufficient_balance"
                if reason:
                    reject(index, now, mint, "buy", reason)
                else:
                    orders[mint] = new_order("buy", mint, now, index, strategy)

        inventory = math.fsum(mark(position, now)[0] for position in positions.values())
        equity = _finite(cash + inventory)
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak * 100 if peak else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        equity_curve.append({
            "ts_ms": now, "event_index": index, "cash_sol": cash,
            "inventory_value_sol": inventory, "equity_sol": equity, "drawdown_pct": drawdown,
        })

    final_ts = observations[-1]["ts_ms"] if observations else None
    open_positions = []
    for mint, position in positions.items():
        value, status = mark(position, final_ts)
        quote = latest[mint]
        open_positions.append({
            **position, "latest_price_sol": quote["price_sol"],
            "latest_quote_ts_ms": quote["ts_ms"], "quote_age_ms": final_ts - quote["ts_ms"],
            "valuation_status": status, "conservative_value_sol": value,
            "unrealized_marked_pnl_sol": value - position["cash_invested_sol"],
            "exit_pending": mint in orders and orders[mint]["side"] == "sell",
        })
    protocol_cost = math.fsum(fill["protocol_fee_sol"] for fill in fills)
    network_cost = math.fsum(fill["network_fee_sol"] for fill in fills)
    win_count = sum(trade["pnl_sol"] > 0 for trade in trades)
    return {
        "schema_version": 1, "mode": "paper_replay", "source_label": cfg["source_label"],
        "strategy": strategy, "parameters": cfg,
        "assumptions": [
            "Synthetic input, when selected, is artificial and proves no profitability.",
            "All price, liquidity, sellability and prior-creator-history fields are supplied observations, not independently verified.",
            "Protocol fee, slippage and network costs are configurable modeling assumptions, not verified live execution terms.",
            "position_sol is an all-in entry budget; buy fee and network cost reduce purchased notional.",
            "Momentum requires 3 distinct-time observations, two positive moves and the configured cumulative return within max_quote_age_ms.",
            "Fills require a later event for that mint after latency; pending entry signals expire after max_quote_age_ms.",
            "Orders fill in full at observed price plus/minus fixed slippage; no depth, curve price impact, MEV or partial-fill model is provided.",
            "When liquidity_sol is supplied, each full fill and mark requires notional <= that quote's liquidity; shared liquidity is not depleted.",
            "Missing sellable defaults to true and missing liquidity imposes no cap; these assumptions do not establish real executability.",
            "Only successful simulated sides incur network costs; rejected attempts have no modeled transaction fees.",
            "Stale, unsellable, under-liquid and uneconomic inventory is marked at zero; executable marks deduct estimated exit costs.",
            "End of input leaves positions and exit orders unresolved; there is no fabricated liquidation.",
            "Win rate uses closed positions only; net_pnl_sol uses conservative equity while realized_pnl_sol uses closed trades.",
            "Drawdown is measured at observed events; missing intervals may hide larger losses.",
        ],
        "event_count": len(observations),
        "start_ts_ms": observations[0]["ts_ms"] if observations else None,
        "end_ts_ms": final_ts, "initial_balance_sol": cfg["initial_balance_sol"],
        "cash_sol": cash,
        "conservative_inventory_value_sol": equity_curve[-1]["inventory_value_sol"],
        "conservative_equity_sol": equity_curve[-1]["equity_sol"],
        "net_pnl_sol": equity_curve[-1]["equity_sol"] - cfg["initial_balance_sol"],
        "realized_pnl_sol": math.fsum(trade["pnl_sol"] for trade in trades),
        "realized_cashflow_sol": cash - cfg["initial_balance_sol"],
        "max_drawdown_pct": max_drawdown,
        "closed_trade_count": len(trades), "win_count": win_count,
        "win_rate": win_count / len(trades) if trades else None,
        "fill_count": len(fills),
        "costs": {
            "protocol_fees_sol": protocol_cost, "network_fees_sol": network_cost,
            "total_explicit_fees_sol": protocol_cost + network_cost,
            "modeled_slippage_sol": math.fsum(fill["modeled_slippage_sol"] for fill in fills),
        },
        "fills": fills, "trades": trades, "rejections": rejections,
        "open_positions": open_positions, "pending_orders": list(orders.values()),
        "equity_curve": equity_curve,
    }
