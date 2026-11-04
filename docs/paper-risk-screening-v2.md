# Experimental paper risk screening v2

This feature is **wired into paper replay** through `config/paper.json`. It
never connects a wallet, signs a transaction, or submits an exchange order.
The existing synthetic demonstration uses explicitly fabricated concentration
values. Nothing here establishes a trading edge.

## Active paper defaults

| Parameter | Default | Behavior |
| --- | --- | --- |
| `position_fraction` | `0.02` | Reserve 2% of conservative virtual equity at decision time, including only executable estimated inventory |
| `enforce_holder_screen` | `true` | Reject candidate entry when required evidence is missing or above thresholds |
| `top10_holder_limit` | `0.25` | Top ten declared beneficial owners may account for at most 25% |
| `developer_holder_limit` | `0.05` | Developer-associated owners may account for at most 5% |
| `stop_loss_bps` | `3500` | Trigger an exit request at 35% modeled loss; fill only on a subsequent executable observation |
| `max_positions` | `3` | Maximum paper inventory slots, including pending buys |
| `position_sol` | `0.05` | Fallback fixed allocation only when `position_fraction=0` |

Entry signals require evidence in the **current price event**:

```json
{"ts_ms":2000,"mint":"SYNTH_EXAMPLE","price_sol":0.000001,"top10_holder_fraction":0.2,"developer_holder_fraction":0.02}
```

Required fractions are real JSON numbers in the range 0 to 1, not 20 or 2
percentage points. The replay never infers a missing developer owner as zero.
Evidence is checked at signal time; it is **not rechecked at simulated fill**.
A fresh signal-time snapshot must be supplied by the upstream recorder.

## Account-level ownership analysis (read-only)

```bash
PYTHONPATH=src python3 -m pumpfun_lab screen \
  --snapshot data/fixtures/synthetic_holders.json \
  --equity-sol 10 --output output/holder-screen
```

The synthetic file is a deliberately fictional *complete* snapshot, not a real
token. Real JSON needs:

- `mint`, `source_label`, `observed_ts_ms` (integer milliseconds)
- `total_supply_raw` (integer string or integer)
- `accounts_complete: true` and `developer_owners_verified: true`
- `developer_owners` (verified, nonempty list of owner identifiers)
- `token_accounts` (nonempty list of distinct `token_account` keys,
  each with `owner` and exact `amount_raw`)

All balances must sum to total supply. No unobserved supply, accounts or
identities are silently filled in. Multiple token accounts for the same
declared owner are aggregated. The top-ten concentration includes all accounts,
including vaults, unless the data provider has already adjusted its declared
denominator **before** creating a verified snapshot. This module does not
make such adjustments and does not perform developer-wallet clustering.
A claim of verified beneficial ownership is not independently authenticated.

The resulting `report.json` contains calculated fractions and an eligibility
decision. Copy normalized numeric fractions to timestamp-aligned market
observations *only if they were known at the decision time*. A snapshot taken
after a price event cannot be used for that earlier event.

## Decisions and limitations

Paper replay emits a structured `decision_journal` for momentum candidates,
including `ts_ms`, mint, strategy, source label, observed concentration,
approval/rejection, and paper budget. Signal-time decisions remain immutable
after later observations. The existing replay's separate `fills`, `trades`,
`rejections`, and `equity_curve` track paper outcomes.

Existing creator-history fields are **caller supplied** and are not independent
developer-history verification. A live WebSocket feed currently supplies
neither holder concentration nor verified developer ownership, so new paper
entries from that feed are correctly rejected when the gate is on.

AI strategy training/shadow deployment, durable background hosting and a
continuously updating web dashboard are **not part of this change**. GitHub
Actions run bounded CI jobs, not an always-on paper bot.

Run unit tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

No private keys, wallet signing, live transaction routing or order execution
are included. A 35% stop is not a promise of an exit within 35%; a gap can
produce a much worse simulated sale.
