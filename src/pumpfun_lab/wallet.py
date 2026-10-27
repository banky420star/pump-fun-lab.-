"""Bounded, read-only Solana wallet evidence. No signing or PnL inference."""

from __future__ import annotations

import json
import math
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any


_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
DEFAULT_RPC_URL = "https://api.mainnet.solana.com"
_READ_METHODS = frozenset({"getSignaturesForAddress", "getTransaction"})
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
Transport = Callable[[str, dict[str, Any], float], Mapping[str, Any]]
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
PUMP_PROGRAMS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump bonding curve",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap",
}
PROGRAM_ID_SOURCES = [
    "https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/PUMP_PROGRAM_README.md",
    "https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/PUMP_SWAP_README.md",
]


def validate_address(address: str) -> bytes:
    """Decode a base58 Solana public address and require exactly 32 bytes.

    This is a format check; it does not establish wallet ownership or control.
    """
    if not isinstance(address, str) or not 32 <= len(address) <= 44:
        raise ValueError("A Solana address must encode exactly 32 bytes")
    value = 0
    for character in address:
        digit = _BASE58.find(character)
        if digit < 0:
            raise ValueError("A Solana address must use the base58 alphabet")
        value = value * 58 + digit
    leading_zeroes = len(address) - len(address.lstrip("1"))
    decoded = bytes(leading_zeroes) + value.to_bytes((value.bit_length() + 7) // 8, "big")
    if len(decoded) != 32:
        raise ValueError("A Solana address must encode exactly 32 bytes")
    return decoded


class RpcError(RuntimeError):
    """A sanitized transport/RPC error safe to include in an evidence report."""

    def __init__(self, category: str, message: str, code: int | None = None):
        super().__init__(message)
        self.category = category
        self.code = code

    def as_dict(self) -> dict[str, Any]:
        return {"category": self.category, "message": str(self), "code": self.code}


def _http_transport(url: str, payload: dict[str, Any], timeout: float) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": "pumpfun-paper-lab-read-only/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise RpcError("response_too_large", "RPC response exceeded the 8 MiB limit")
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        raise RpcError("invalid_response", "RPC response was not valid JSON") from None


class RpcClient:
    """JSON-RPC client with an immutable read-method allowlist and no retries.

    The injectable transport is the network boundary. It receives
    ``(url, request_dict, timeout_seconds)`` and returns a decoded JSON-RPC
    envelope. It is responsible for honoring its timeout when injected.
    URLs, exception text, and server error messages are never logged.
    """

    def __init__(self, rpc_url: str | None = None, *, timeout: float = 10.0,
                 max_calls: int = 21, transport: Transport | None = None):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 30):
            raise ValueError("RPC timeout must be finite and between 0 and 30 seconds")
        if type(max_calls) is not int or not 1 <= max_calls <= 21:
            raise ValueError("RPC call budget must be an integer between 1 and 21")
        endpoint = rpc_url if rpc_url is not None else (os.environ.get("SOLANA_RPC_URL", "").strip() or DEFAULT_RPC_URL)
        try:
            parsed = urllib.parse.urlsplit(endpoint)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                raise ValueError
            host = parsed.hostname
            if ":" in host:
                host = "[" + host + "]"
            origin = parsed.scheme + "://" + host
            if parsed.port is not None:
                origin += ":" + str(parsed.port)
        except (ValueError, AttributeError, TypeError):
            raise ValueError("RPC endpoint must be an HTTP(S) URL with a host") from None
        self._rpc_url = endpoint
        self.endpoint_origin = origin
        self.timeout = float(timeout)
        self.max_calls = max_calls
        self.calls_made = 0
        self.records: list[dict[str, Any]] = []
        self._transport = transport or _http_transport
        self.transport_kind = "injected" if transport is not None else "http_json_rpc"

    def call(self, method: str, params: list[Any]) -> Any:
        if method not in _READ_METHODS:
            raise ValueError("RPC method is not on the read-only allowlist")
        if self.calls_made >= self.max_calls:
            raise RpcError("call_budget_exhausted", "RPC call budget has been exhausted")
        self.calls_made += 1
        payload = {"jsonrpc": "2.0", "id": self.calls_made, "method": method, "params": params}
        record = {"request": payload, "started_at_utc": _utc_now(), "outcome": "pending"}
        self.records.append(record)
        start = time.monotonic()
        error: RpcError | None = None
        result = None
        try:
            response = self._transport(self._rpc_url, payload, self.timeout)
            if (not isinstance(response, Mapping) or response.get("jsonrpc") != "2.0"
                    or type(response.get("id")) is not int or response.get("id") != payload["id"]):
                raise RpcError("invalid_response", "RPC response did not match the request")
            if response.get("error") is not None:
                rpc_error = response["error"]
                code = rpc_error.get("code") if isinstance(rpc_error, Mapping) else None
                raise RpcError("rpc_error", "RPC server returned an error; message omitted",
                               code if type(code) is int else None)
            if "result" not in response:
                raise RpcError("invalid_response", "RPC response did not contain a result")
            result = response["result"]
            record["outcome"] = "ok"
            record["response_id"] = response["id"]
        except RpcError as exc:
            error = exc
        except urllib.error.HTTPError as exc:
            error = RpcError("http_error", "RPC HTTP request failed", exc.code)
        except (TimeoutError, socket.timeout):
            error = RpcError("timeout", "RPC request timed out")
        except urllib.error.URLError:
            error = RpcError("network_error", "RPC endpoint could not be reached")
        except Exception:
            # Exception strings can contain the complete credential-bearing URL.
            error = RpcError("transport_error", "RPC transport failed; details omitted")
        record["elapsed_ms"] = round((time.monotonic() - start) * 1000, 3)
        if error is not None:
            record["outcome"] = "error"
            record["error"] = error.as_dict()
            raise error from None
        return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _uint(value: Any) -> bool:
    return type(value) is int and value >= 0


def _decimal_amount(amount: int, decimals: int) -> str:
    digits = str(abs(amount)).zfill(decimals + 1)
    if decimals:
        digits = (digits[:-decimals] + "." + digits[-decimals:]).rstrip("0").rstrip(".")
    return ("-" if amount < 0 else "") + digits


def _account_keys(message: Mapping[str, Any], meta: Mapping[str, Any],
                  flags: set[str]) -> list[str | None]:
    source = message.get("accountKeys")
    if not isinstance(source, list):
        flags.add("account_keys_unavailable")
        return []
    parsed_encoding = any(isinstance(key, Mapping) for key in source)
    keys: list[str | None] = []
    for key in source:
        candidate = key.get("pubkey") if isinstance(key, Mapping) else key
        if not isinstance(candidate, str) or not candidate:
            flags.add("malformed_account_key")
            candidate = None
        keys.append(candidate)
    # jsonParsed already expands lookup-table keys; raw json requires this order.
    if not parsed_encoding:
        loaded = meta.get("loadedAddresses")
        if isinstance(loaded, Mapping):
            for kind in ("writable", "readonly"):
                addresses = loaded.get(kind)
                if not isinstance(addresses, list):
                    flags.add("loaded_addresses_unavailable")
                    continue
                for key in addresses:
                    if not isinstance(key, str) or not key:
                        flags.add("malformed_account_key")
                        key = None
                    keys.append(key)
        elif message.get("addressTableLookups"):
            flags.add("loaded_addresses_unavailable")
    return keys


def _token_deltas(meta: Mapping[str, Any], address: str, account_count: int,
                  flags: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unresolved: list[dict[str, Any]] = []
    pre, post = meta.get("preTokenBalances"), meta.get("postTokenBalances")
    if not isinstance(pre, list) or not isinstance(post, list):
        flags.add("token_balance_data_unavailable")
        for phase, records in (("pre", pre), ("post", post)):
            if isinstance(records, list):
                unresolved.extend({"phase": phase, "reason": "token_balance_data_unavailable",
                                   "record": record} for record in records)
        return [], unresolved

    entries: list[tuple[str, Mapping[str, Any], int | None]] = []
    invalid_indexes: dict[int, str] = {}
    for phase, records in (("pre", pre), ("post", post)):
        seen: set[int] = set()
        for record in records:
            if not isinstance(record, Mapping):
                flags.add("malformed_token_balance")
                unresolved.append({"phase": phase, "reason": "malformed_token_balance", "record": record})
                continue
            index = record.get("accountIndex")
            if not _uint(index) or index >= account_count:
                flags.add("malformed_token_balance")
                unresolved.append({"phase": phase, "reason": "malformed_token_balance", "record": record})
                continue
            entries.append((phase, record, index))
            if index in seen:
                invalid_indexes[index] = "duplicate_token_balance"
            seen.add(index)
            amount = record.get("uiTokenAmount")
            raw = amount.get("amount") if isinstance(amount, Mapping) else None
            decimals = amount.get("decimals") if isinstance(amount, Mapping) else None
            valid_amount = (isinstance(raw, str) and raw.isascii() and raw.isdigit()
                            and len(raw) <= 20 and int(raw) <= 18446744073709551615)
            if (not isinstance(record.get("mint"), str) or not record["mint"]
                    or not valid_amount or not _uint(decimals) or decimals > 255):
                invalid_indexes[index] = "malformed_token_balance"
            elif not isinstance(record.get("owner"), str) or not record["owner"]:
                invalid_indexes[index] = "unknown_token_owner"

    groups: dict[tuple[str, str], list[tuple[str, Mapping[str, Any], int]]] = {}
    for phase, record, index in entries:
        if index in invalid_indexes:
            reason = invalid_indexes[index]
            flags.add(reason)
            unresolved.append({"phase": phase, "reason": reason, "record": record})
        elif record["owner"] == address:
            groups.setdefault((record["mint"], address), []).append((phase, record, index))

    deltas: list[dict[str, Any]] = []
    for (mint, owner), records in sorted(groups.items()):
        decimals_set = {record["uiTokenAmount"]["decimals"] for _, record, _ in records}
        if len(decimals_set) != 1:
            flags.add("token_decimals_mismatch")
            unresolved.extend({"phase": phase, "reason": "token_decimals_mismatch", "record": record}
                              for phase, record, _ in records)
            continue
        decimals = decimals_set.pop()
        before = sum(int(record["uiTokenAmount"]["amount"]) for phase, record, _ in records if phase == "pre")
        after = sum(int(record["uiTokenAmount"]["amount"]) for phase, record, _ in records if phase == "post")
        deltas.append({"mint": mint, "owner": owner, "decimals": decimals,
                       "pre_amount_raw": str(before), "post_amount_raw": str(after),
                       "delta_raw": str(after - before),
                       "delta_token_amount": _decimal_amount(after - before, decimals),
                       "account_indexes": sorted({index for _, _, index in records}),
                       "is_wrapped_sol": mint == WRAPPED_SOL_MINT})
    return deltas, unresolved


def _pump_references(message: Mapping[str, Any], meta: Mapping[str, Any],
                     keys: list[str | None], flags: set[str]) -> list[dict[str, Any]]:
    references: dict[str, set[str]] = {}
    instruction_groups: list[tuple[str, Any]] = [("outer_instruction", message.get("instructions"))]
    inner = meta.get("innerInstructions")
    if isinstance(inner, list):
        for group in inner:
            if isinstance(group, Mapping):
                instruction_groups.append(("inner_instruction", group.get("instructions")))
            else:
                flags.add("inner_instruction_data_unavailable")
    else:
        flags.add("inner_instruction_data_unavailable")
    for evidence, instructions in instruction_groups:
        if not isinstance(instructions, list):
            flags.add("instruction_data_unavailable")
            continue
        for instruction in instructions:
            if not isinstance(instruction, Mapping):
                flags.add("malformed_instruction")
                continue
            program_id = instruction.get("programId")
            if not isinstance(program_id, str):
                index = instruction.get("programIdIndex")
                if not _uint(index) or index >= len(keys):
                    flags.add("unresolved_program_index")
                    continue
                program_id = keys[index]
            if program_id in PUMP_PROGRAMS:
                references.setdefault(program_id, set()).add(evidence)
    return [{"program_id": program_id, "name": PUMP_PROGRAMS[program_id], "evidence": sorted(evidence)}
            for program_id, evidence in sorted(references.items())]


def normalize_transaction(transaction: Mapping[str, Any] | None, address: str, *,
                          signature: str | None = None,
                          signature_info: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Normalize observed native/token balance changes, never classify them as trades.

    Token quantities are exact decimal strings. The native delta already includes
    fees, rent, transfers, and other native changes affecting the address. The
    token totals cover involved accounts only, not the wallet's entire holdings.
    """
    validate_address(address)
    info = signature_info if isinstance(signature_info, Mapping) else {}
    tx = transaction if isinstance(transaction, Mapping) else {}
    body = tx.get("transaction")
    body = body if isinstance(body, Mapping) else {}
    signatures = body.get("signatures")
    observed_signature = signatures[0] if isinstance(signatures, list) and signatures else None
    message = body.get("message")
    message = message if isinstance(message, Mapping) else {}
    metadata = tx.get("meta")
    meta = metadata if isinstance(metadata, Mapping) else {}
    flags: set[str] = set()
    status_source = "transaction_meta" if "err" in meta else "signature_record" if "err" in info else "unavailable"
    error = meta.get("err") if "err" in meta else info.get("err")
    status = "unknown" if status_source == "unavailable" else "success" if error is None else "failure"
    block_time = tx.get("blockTime") if tx.get("blockTime") is not None else info.get("blockTime")
    slot = tx.get("slot", info.get("slot"))
    row: dict[str, Any] = {
        "signature": signature or observed_signature or info.get("signature"),
        "slot": slot if _uint(slot) else None,
        "block_time": block_time if type(block_time) is int else None,
        "transaction_available": isinstance(transaction, Mapping),
        "status": status, "status_source": status_source, "transaction_error": error,
        "fee_lamports": None, "fee_payer": None, "wallet_fee_lamports": None,
        "wallet_sol_delta_lamports": None, "wallet_sol_delta_sol": None,
        "token_deltas": [], "unresolved_token_balances": [],
        "pump_program_instruction_references": [],
        "activity_classification": "unclassified", "data_quality_flags": [],
    }
    if not isinstance(transaction, Mapping):
        flags.add("transaction_unavailable")
    elif not isinstance(metadata, Mapping):
        flags.add("transaction_metadata_unavailable")
    elif signature and observed_signature and signature != observed_signature:
        flags.add("transaction_signature_mismatch")
    else:
        keys = _account_keys(message, meta, flags)
        row["fee_payer"] = keys[0] if keys else None
        if _uint(meta.get("fee")):
            row["fee_lamports"] = meta["fee"]
            if row["fee_payer"] is not None:
                row["wallet_fee_lamports"] = meta["fee"] if row["fee_payer"] == address else 0
        else:
            flags.add("transaction_fee_unavailable")
        indexes = [index for index, key in enumerate(keys) if key == address]
        pre, post = meta.get("preBalances"), meta.get("postBalances")
        if (len(indexes) == 1 and isinstance(pre, list) and isinstance(post, list)
                and indexes[0] < len(pre) and indexes[0] < len(post)
                and _uint(pre[indexes[0]]) and _uint(post[indexes[0]])):
            delta = post[indexes[0]] - pre[indexes[0]]
            row["wallet_sol_delta_lamports"] = delta
            row["wallet_sol_delta_sol"] = _decimal_amount(delta, 9)
        else:
            flags.add("wallet_sol_balance_unavailable")
            if not indexes:
                flags.add("wallet_not_in_account_keys")
            elif len(indexes) > 1:
                flags.add("ambiguous_wallet_account_index")
        row["token_deltas"], row["unresolved_token_balances"] = _token_deltas(meta, address, len(keys), flags)
        row["pump_program_instruction_references"] = _pump_references(message, meta, keys, flags)
    if row["block_time"] is None:
        flags.add("block_time_unavailable")
    row["data_quality_flags"] = sorted(flags)
    return row


def fetch_wallet_history(address: str, rpc_url: str | None = None,
                         max_signatures: int = 100, max_transactions: int = 20, *,
                         transport: Transport | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch one bounded signature page and a newest-first transaction sample.

    There is no pagination, retry, account discovery, account mutation, or
    transaction submission. Network failures return an evidence report, while
    invalid local configuration raises ValueError before any request.
    """
    validate_address(address)
    if type(max_signatures) is not int or not 1 <= max_signatures <= 100:
        raise ValueError("max_signatures must be an integer between 1 and 100")
    if type(max_transactions) is not int or not 0 <= max_transactions <= 20:
        raise ValueError("max_transactions must be an integer between 0 and 20")
    client = RpcClient(rpc_url, timeout=timeout, max_calls=1 + max_transactions, transport=transport)
    started_at = _utc_now()
    raw: dict[str, Any] = {"signatures": [], "transactions": []}
    errors: list[dict[str, Any]] = []
    flags = {"limited_lookback", "token_account_history_not_queried"}
    signature_query_succeeded = False
    try:
        result = client.call("getSignaturesForAddress", [address, {"commitment": "finalized", "limit": max_signatures}])
        if not isinstance(result, list):
            raw["unrecognized_signature_result"] = result
            raise RpcError("invalid_response", "Signature query returned a non-array result")
        raw["signatures"] = result
        signature_query_succeeded = True
        if len(result) > max_signatures:
            flags.add("rpc_signature_limit_exceeded")
    except RpcError as exc:
        errors.append({"method": "getSignaturesForAddress", **exc.as_dict()})
        flags.add("signature_query_failed")

    unique_signatures: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    duplicate_count = malformed_count = 0
    for record in raw["signatures"][:max_signatures]:
        signature = record.get("signature") if isinstance(record, Mapping) else None
        if not isinstance(signature, str) or not signature or len(signature) > 128:
            malformed_count += 1
            flags.add("malformed_signature_records")
            continue
        if signature in seen:
            duplicate_count += 1
            flags.add("duplicate_signatures")
            continue
        seen.add(signature)
        unique_signatures.append(record)

    rows: list[dict[str, Any]] = []
    fetched_count = missing_count = 0
    for info in unique_signatures[:max_transactions]:
        signature = info["signature"]
        transaction_result = None
        failure: RpcError | None = None
        try:
            transaction_result = client.call("getTransaction", [signature, {
                "commitment": "finalized", "encoding": "json", "maxSupportedTransactionVersion": 0}])
            if transaction_result is not None and not isinstance(transaction_result, Mapping):
                raise RpcError("invalid_response", "Transaction query returned an unrecognized result")
        except RpcError as exc:
            failure = exc
            errors.append({"method": "getTransaction", "signature": signature, **exc.as_dict()})
        raw["transactions"].append({"signature": signature, "result": transaction_result,
                                     "error": failure.as_dict() if failure else None})
        if failure is None and isinstance(transaction_result, Mapping):
            fetched_count += 1
        else:
            missing_count += 1
        row = normalize_transaction(transaction_result if failure is None else None, address,
                                    signature=signature, signature_info=info)
        if failure is not None:
            row["data_quality_flags"] = sorted(set(row["data_quality_flags"]) | {"transaction_query_failed"})
        rows.append(row)
        flags.update(row["data_quality_flags"])
        # Stop after a transport/HTTP failure; do not hammer an unavailable or
        # rate-limited endpoint. A transaction-specific RPC error can be sampled past.
        if failure is not None and failure.category in {"network_error", "transport_error", "timeout", "http_error"}:
            flags.add("transaction_sampling_stopped_on_network_error")
            break

    times = [record["blockTime"] for record in unique_signatures if type(record.get("blockTime")) is int]
    requested_count = len(raw["transactions"])
    unfetched_count = len(unique_signatures) - requested_count
    if unfetched_count:
        flags.add("transaction_sample_limited")
    return {
        "schema_version": 1,
        "address": address,
        "provenance": {
            "started_at_utc": started_at, "finished_at_utc": _utc_now(),
            "rpc_origin": client.endpoint_origin, "endpoint_path_and_query_omitted": True,
            "transport": client.transport_kind,
            "commitment": "finalized", "transaction_encoding": "json",
            "max_supported_transaction_version": 0,
            "cluster_verified": False,
            "calls_made": client.calls_made, "call_budget": client.max_calls,
            "timeout_seconds": client.timeout, "requests": client.records,
            "read_only_methods": sorted(_READ_METHODS),
            "program_id_sources": PROGRAM_ID_SOURCES,
            "program_ids_verified_on": "2026-10-27",
        },
        "completeness": {
            "history_complete": False, "pnl_ready": False, "lookback_limited": True,
            "scope": "Transactions referencing this wallet address; token-account-only histories are not discovered",
            "signature_query_succeeded": signature_query_succeeded,
            "signature_limit_requested": max_signatures,
            "transaction_limit_requested": max_transactions,
            "signature_records_returned": len(raw["signatures"]),
            "signatures_observed": len(unique_signatures),
            "duplicate_signature_count": duplicate_count,
            "malformed_signature_count": malformed_count,
            "signature_limit_reached": len(raw["signatures"]) >= max_signatures,
            "transactions_requested": requested_count,
            "transactions_fetched": fetched_count,
            "missing_transaction_count": missing_count,
            "unfetched_signature_count": unfetched_count,
            "all_observed_signatures_fetched": signature_query_succeeded and not missing_count and not unfetched_count,
            "oldest_observed_block_time": min(times) if times else None,
            "newest_observed_block_time": max(times) if times else None,
            "data_quality_flags": sorted(flags),
        },
        "raw": raw, "rows": rows, "errors": errors,
    }
