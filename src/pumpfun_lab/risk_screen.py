"""Read-only, deterministic token screening for paper strategy experiments.

No RPC calls, wallet access, signing or order submission.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping


@dataclass(frozen=True)
class ScreenDecision:
    eligible: bool
    reasons: tuple[str, ...]
    position_sol: Decimal


def _fraction(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0 or number > 1:
        return None
    return number


def screen_token(
    evidence: Mapping[str, object],
    equity_sol: object,
    *,
    position_fraction: str = "0.02",
    max_top10_fraction: str = "0.25",
    max_developer_fraction: str = "0.05",
) -> ScreenDecision:
    """Fail closed on missing/invalid concentration evidence.

    Fractions are in [0, 1], not percentage points. Position sizing is
    a paper allocation cap, not a loss-at-stop risk budget.
    """
    reasons: list[str] = []
    top10 = _fraction(evidence.get("top10_holder_fraction"))
    developer = _fraction(evidence.get("developer_holder_fraction"))
    if top10 is None:
        reasons.append("missing_or_invalid_top10_concentration")
    elif top10 > Decimal(max_top10_fraction):
        reasons.append("top10_concentration_exceeded")
    if developer is None:
        reasons.append("missing_or_invalid_developer_ownership")
    elif developer > Decimal(max_developer_fraction):
        reasons.append("developer_ownership_exceeded")
    try:
        equity = Decimal(str(equity_sol))
        fraction = Decimal(position_fraction)
        if not equity.is_finite() or equity <= 0 or not fraction.is_finite() or not (0 < fraction <= 1):
            raise ValueError("invalid equity or fraction")
    except (InvalidOperation, ValueError, TypeError):
        reasons.append("invalid_paper_equity")
        equity = Decimal("0")
        fraction = Decimal("0")
    return ScreenDecision(not reasons, tuple(reasons), equity * fraction if not reasons else Decimal("0"))
