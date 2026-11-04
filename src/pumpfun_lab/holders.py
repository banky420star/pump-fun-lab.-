"""Read-only holder-distribution accounting from complete supplied snapshots.

This module never fetches a chain endpoint or infers developer identities.
All snapshots are externally sourced assertions; failed completeness is fatal.
"""
from collections import defaultdict
from decimal import Decimal
import re

from .risk_screen import screen_token


def _raw_uint(value, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a nonnegative raw integer")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise ValueError(f"{label} must be a nonnegative raw integer")


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value.strip()


def analyze_holder_snapshot(snapshot: dict, *, equity_sol: object = "10") -> dict:
    """Aggregate token accounts by declared beneficial owner without guessing.

    Exact raw-unit arithmetic avoids floating point truncation. The caller must
    supply every token account, the entire supply, and verified developer owner
    identifiers as known at the snapshot timestamp. Burn/vault/escrow balances
    are NOT implicitly ignored; missing/partial supply is rejected.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("holder snapshot must be an object")
    mint = _text(snapshot.get("mint"), "mint")
    source_label = _text(snapshot.get("source_label"), "source_label")
    observed = snapshot.get("observed_ts_ms")
    if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0:
        raise ValueError("observed_ts_ms must be a nonnegative integer")
    if snapshot.get("accounts_complete") is not True:
        raise ValueError("accounts_complete must be explicitly true")
    if snapshot.get("developer_owners_verified") is not True:
        raise ValueError("developer_owners_verified must be explicitly true")
    supply = _raw_uint(snapshot.get("total_supply_raw"), "total_supply_raw")
    if supply <= 0:
        raise ValueError("total_supply_raw must be positive")
    accounts = snapshot.get("token_accounts")
    if not isinstance(accounts, list) or not accounts:
        raise ValueError("token_accounts must be a nonempty array")
    developers = snapshot.get("developer_owners")
    if not isinstance(developers, list):
        raise ValueError("developer_owners must be an array")
    dev_set = {_text(owner, "developer_owners entry") for owner in developers}
    if len(dev_set) != len(developers):
        raise ValueError("developer_owners contains duplicates")
    if not dev_set:
        raise ValueError("developer_owners must identify at least one verified owner")

    owners: dict[str, int] = defaultdict(int)
    seen_accounts: set[str] = set()
    for index, account in enumerate(accounts):
        if not isinstance(account, dict):
            raise ValueError(f"token_accounts[{index}] must be an object")
        account_id = _text(account.get("token_account"), f"token_accounts[{index}].token_account")
        owner = _text(account.get("owner"), f"token_accounts[{index}].owner")
        if account_id in seen_accounts:
            raise ValueError(f"duplicate token account at index {index}")
        seen_accounts.add(account_id)
        amount = _raw_uint(account.get("amount_raw"), f"token_accounts[{index}].amount_raw")
        owners[owner] += amount
    accounted = sum(owners.values())
    if accounted != supply:
        raise ValueError("accounted balances do not equal declared total supply; incomplete or inconsistent snapshot")

    top10 = sum(sorted(owners.values(), reverse=True)[:10])
    dev = sum(owners.get(owner, 0) for owner in dev_set)
    top10_fraction = Decimal(top10) / Decimal(supply)
    developer_fraction = Decimal(dev) / Decimal(supply)
    screen = screen_token(
        {"top10_holder_fraction": str(top10_fraction),
         "developer_holder_fraction": str(developer_fraction)},
        equity_sol,
    )
    return {
        "schema_version": 1,
        "mode": "read_only_holder_screen",
        "mint": mint, "source_label": source_label,
        "observed_ts_ms": observed, "account_count": len(seen_accounts),
        "distinct_owner_count": len(owners),
        "total_supply_raw": str(supply),
        "top10_holder_fraction": str(top10_fraction),
        "developer_holder_fraction": str(developer_fraction),
        "decision": {
            "eligible": screen.eligible, "reasons": list(screen.reasons),
            "virtual_position_sol": str(screen.position_sol),
        },
        "assumptions": [
            "Snapshot completeness and developer-owner linkage are supplied claims, not chain verified.",
            "All token accounts including declared vaults are included; no automatic exclusions.",
            "Repeated token accounts are rejected and balances are grouped by supplied owner identity.",
            "No developer-associated wallet clustering is inferred.",
            "Historical screening must use a snapshot observed no later than the strategy decision time.",
            "An eligible paper screen does not imply market liquidity, safe execution or profitability.",
        ],
    }
