"""Bounded PumpPortal market observation, with no transaction methods.

Discovery subscribes only to the provider's free creation/migration feeds.
Trade observations require explicit data-charge consent and a credential. The
credential itself can authorize trading outside this application; it is never
saved, logged, or treated as a read-only credential.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
import time
from urllib.parse import quote

from .wallet import validate_address, WRAPPED_SOL_MINT


_ENDPOINT = "wss://pumpportal.fun/api/data"
_MAX_MESSAGE_BYTES = 65536
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_KINDS = {"create": "new_token", "migration": "migration", "migrate": "migration",
          "buy": "buy", "sell": "sell"}


def _bounded_integer(value, label, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be an integer between 1 and {maximum}")


def _utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _signature(value, secret):
    """Keep only a 64-byte base58 signature; never reflect a known secret."""
    if (not isinstance(value, str) or not 64 <= len(value) <= 88
            or (secret and secret in value)):
        return None
    decoded = 0
    for character in value:
        digit = _BASE58.find(character)
        if digit < 0:
            return None
        decoded = decoded * 58 + digit
    length = len(value) - len(value.lstrip("1")) + (decoded.bit_length() + 7) // 8
    return value if length == 64 else None


def _sol_quote(payload):
    # Provider SOL-named reserves are the only implicit denomination evidence.
    # Explicit conflicting quote metadata always prevents price normalization.
    for field in ("quoteMint", "quote_mint", "quoteTokenMint"):
        if field in payload and payload[field] != WRAPPED_SOL_MINT:
            return False
    for field in ("quoteCurrency", "quote_currency", "quoteSymbol"):
        if field in payload and payload[field] not in ("SOL", "WSOL", "wSOL"):
            return False
    return True


def _decode(message):
    def nonfinite(_):
        raise ValueError("nonfinite JSON")
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON")
        return number
    if isinstance(message, bytes):
        if len(message) > _MAX_MESSAGE_BYTES:
            raise ValueError("oversized message")
        message = message.decode("utf-8")
    if not isinstance(message, str) or len(message.encode("utf-8")) > _MAX_MESSAGE_BYTES:
        raise ValueError("invalid message")
    payload = json.loads(message, parse_constant=nonfinite, parse_float=finite_float)
    if not isinstance(payload, dict):
        raise ValueError("message must be an object")
    return payload


def _provider_error(payload):
    if (payload.get("error") or payload.get("errors") or payload.get("success") is False
            or payload.get("status") in ("error", "failed", "unauthorized")):
        return True
    message = payload.get("message")
    if isinstance(message, str):
        lower = message.lower()
        return any(term in lower for term in
                   ("error", "insufficient", "invalid", "unauthorized", "forbidden", "api key", "rate limit"))
    return False


def capture_market(*, mode="discovery", duration_seconds=90, max_messages=1000,
                   max_tokens=10, api_key=None, accept_data_charges=False,
                   connect_factory=None, clock=time.monotonic, wall_clock=time.time):
    """Capture one bounded connection and return sanitized, JSON-safe evidence.

    ``connect_factory`` is the websocket boundary and must support the library's
    context-manager, ``send`` and ``recv(timeout=...)`` contract. No retry or
    reconnect occurs. A lost connection leaves the partial capture intact.
    Network/provider failures are evidence in the result; invalid configuration
    raises ValueError before any connection. No arbitrary provider text is kept.
    """
    if mode not in ("discovery", "trades"):
        raise ValueError("mode must be discovery or trades")
    _bounded_integer(duration_seconds, "duration_seconds", 900)
    _bounded_integer(max_messages, "max_messages", 10000)
    _bounded_integer(max_tokens, "max_tokens", 20)
    if type(accept_data_charges) is not bool:
        raise ValueError("accept_data_charges must be a boolean")
    secret = api_key.strip() if isinstance(api_key, str) else None
    if mode == "trades" and (not secret or len(secret) > 4096 or not accept_data_charges):
        raise ValueError("Trade data requires an API key and explicit acceptance of metered data charges")

    start = clock()
    epoch = wall_clock()
    deadline = start + duration_seconds
    counts = dict.fromkeys(("messages", "new_token", "migration", "buy", "sell", "invalid",
                            "duplicates", "acknowledgements", "unsupported_price", "unsubscribed_trade"), 0)
    report = {
        "schema_version": 1,
        "source": "PumpPortal live websocket",
        "transport": "injected" if connect_factory is not None else "websockets",
        "mode": mode,
        "started_at_utc": _utc(epoch),
        "ended_at_utc": None,
        "duration_seconds": 0,
        "requested_duration_seconds": duration_seconds,
        "limits": {"max_messages": max_messages, "max_tokens": max_tokens},
        "connected": False,
        "market_data_received": False,
        "counts": counts,
        "subscribed_mints": [],
        "events": [],
        "observations": [],
        "end_reason": "duration_limit",
        "errors": [],
        "completeness": {
            "history_complete": False,
            "chain_verified": False,
            "finality_verified": False,
            "complete_market_coverage": False,
            "executable_quotes": False,
        },
        "assumptions": [
            "One bounded provider connection; no historical backfill or reconnect. Gaps are not repaired.",
            "Provider events are not independently chain-verified or confirmed finalized.",
            "Only allowlisted event fields are retained; raw provider payloads and wallet identities are omitted.",
            "Timestamps are local receive times anchored to a monotonic clock, not chain execution times.",
            "Trade prices use SOL-named virtual reserves as indicative ratios, not executable quotes.",
            "The provider event schema is not a versioned contract; unsupported or invalid observations are excluded.",
            "Liquidity, sellability, creator history and graduation progress are not inferred.",
            "Discovery has no trade-price series and cannot evaluate a trading strategy.",
            "Trade mode follows only the first capped set of newly observed mints, which creates selection limits.",
            "Received-message limits do not guarantee a provider billing cap: buffered/in-flight events may be charged.",
            "No transactions are signed or submitted. The API key may authorize trades outside this application.",
        ],
    }

    def fail(reason, message):
        report["end_reason"] = reason
        report["errors"].append({"category": reason, "message": message})

    # A standalone disabled logger cannot forward credential-bearing handshake
    # URLs or remote close reasons to a caller's root logging configuration.
    logger = logging.Logger("pumpfun_lab.silent_websocket")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    logger.disabled = True

    try:
        if connect_factory is None:
            try:
                from websockets.sync.client import connect
            except ImportError:
                fail("dependency_missing", "Install the optional live websocket dependency before connecting")
                return report
            connect_factory = connect
        url = _ENDPOINT
        if mode == "trades":
            url += "?api-key=" + quote(secret, safe="")
        seen_creations = set()
        seen_signatures = set()
        subscribed = set()
        with connect_factory(url, open_timeout=min(10, duration_seconds), close_timeout=2,
                             ping_interval=20, ping_timeout=20, max_size=_MAX_MESSAGE_BYTES,
                             max_queue=4, logger=logger) as connection:
            report["connected"] = True
            connection.send(json.dumps({"method": "subscribeNewToken"}))
            connection.send(json.dumps({"method": "subscribeMigration"}))
            while counts["messages"] < max_messages:
                remaining = deadline - clock()
                if remaining <= 0:
                    break
                try:
                    message = connection.recv(timeout=min(5, remaining))
                except TimeoutError:
                    continue
                received_elapsed = max(0, clock() - start)
                if received_elapsed >= duration_seconds:
                    break
                counts["messages"] += 1
                try:
                    payload = _decode(message)
                except (ValueError, UnicodeError, TypeError, RecursionError):
                    counts["invalid"] += 1
                    continue
                if _provider_error(payload):
                    fail("provider_error", "Provider rejected or interrupted the subscription; details omitted")
                    break
                event_type = payload.get("txType")
                kind = _KINDS.get(event_type) if isinstance(event_type, str) else None
                if kind is None:
                    if isinstance(payload.get("message"), str) and "subscribed" in payload["message"].lower():
                        counts["acknowledgements"] += 1
                    else:
                        counts["invalid"] += 1
                    continue
                mint = payload.get("mint")
                try:
                    validate_address(mint)
                    if secret and secret in mint:
                        raise ValueError("sensitive reflection")
                except (ValueError, TypeError):
                    counts["invalid"] += 1
                    continue
                signature = _signature(payload.get("signature"), secret)
                fingerprint = (signature, mint, kind) if signature else None
                if ((kind == "new_token" and mint in seen_creations)
                        or (fingerprint is not None and fingerprint in seen_signatures)):
                    counts["duplicates"] += 1
                    continue
                if fingerprint is not None:
                    seen_signatures.add(fingerprint)
                if kind == "new_token":
                    seen_creations.add(mint)
                counts[kind] += 1
                report["market_data_received"] = True
                # Round to nearest millisecond to avoid binary float artifacts
                # at an otherwise exact receive-time boundary.
                timestamp = round((epoch + received_elapsed) * 1000)
                event = {"event_type": kind, "mint": mint, "ts_ms": timestamp,
                         "received_at_utc": _utc(epoch + received_elapsed)}
                if signature:
                    event["signature"] = signature
                sol_reserves = _positive(payload.get("vSolInBondingCurve"))
                token_reserves = _positive(payload.get("vTokensInBondingCurve"))
                if sol_reserves is not None:
                    event["vSolInBondingCurve"] = sol_reserves
                if token_reserves is not None:
                    event["vTokensInBondingCurve"] = token_reserves
                report["events"].append(event)

                if kind == "new_token" and mode == "trades" and len(subscribed) < max_tokens:
                    if _sol_quote(payload):
                        connection.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
                        subscribed.add(mint)
                        report["subscribed_mints"].append(mint)
                elif kind in ("buy", "sell"):
                    if mode != "trades" or mint not in subscribed:
                        counts["unsubscribed_trade"] += 1
                        continue
                    if not _sol_quote(payload) or sol_reserves is None or token_reserves is None:
                        counts["unsupported_price"] += 1
                        continue
                    price = sol_reserves / token_reserves
                    if not math.isfinite(price) or price <= 0:
                        counts["unsupported_price"] += 1
                        continue
                    report["observations"].append({"ts_ms": timestamp, "mint": mint, "price_sol": price})
            if counts["messages"] >= max_messages and not report["errors"]:
                report["end_reason"] = "message_limit"
    except KeyboardInterrupt:
        fail("interrupted", "Capture was interrupted; the partial observation sample is retained")
    except Exception:
        # Exceptions and close reasons may contain the API key, complete URL,
        # proxy credentials, or arbitrary remote text. Never stringify them.
        fail("connection_error", "Websocket connection failed or closed; details omitted")
    finally:
        elapsed = max(0, clock() - start)
        report["duration_seconds"] = round(elapsed, 6)
        report["ended_at_utc"] = _utc(epoch + elapsed)
        counts["observations"] = len(report["observations"])
    return report
