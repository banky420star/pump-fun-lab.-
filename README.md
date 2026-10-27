# Pump.fun Lab

**Paper-only strategy research, reproducible execution tests, and read-only wallet analysis.**

This public repository contains synthetic demonstrations and an optional bounded live market collector. No profitable live strategy has been established. The code has no transaction-signing or order-submission path.

**Live connection:** Open **Actions → Live market sample → Run workflow** for a free discovery check. See [connect to live market data](docs/live-market.md) for results, metered trade-data setup and the limits of paper testing.

## Quick start

Python **3.11 or newer**. Offline research has no third-party runtime dependencies. The optional live collector uses the pinned transport in `requirements-live.txt`. Run from the repository root:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m pumpfun_lab demo --output output/demo
```

Open `output/demo/report.html`. The accompanying `report.md` is readable text; `report.json` retains simulated fills, costs, rejected actions, unresolved holdings, and source labels.

The demo combines a fabricated profile with 50 artificial per-coin summaries and a 24-observation artificial market. All demo identities, monetary figures and dates are fictional. The **Paper research checks** workflow uses these fixtures; the separate live workflow labels actual market observations explicitly.

## Components

| Component | Purpose | Evidence limit |
| --- | --- | --- |
| Snapshot reconciliation | Exact decimal arithmetic, mint-based identity, selected-winner concentration and unexplained aggregate differences | Coin summaries are not a complete transaction ledger |
| Paper replay | Compare momentum, creator filtering and pre-graduation entries under common costs and delays | A price-observation model cannot establish executable live returns |
| Read-only wallet importer | Preserve raw RPC evidence, native/token changes, fees, unknown ownership and incomplete history | At most 100 signatures and 20 transactions; no automatic trade or PnL inference |
| Live market sample | Bounded creation/migration capture; explicitly authorized metered trade observations with paper replay | Third-party processed data and indicative prices; no funded execution or proven returns |
| Automated checks | Exercise causality, accounting, missing data, validation and escaping | Engineering tests do not establish a trading edge |

## Paper strategies

1. **Momentum:** three fresh observations, two successive positive moves, and at least 5% cumulative movement.
2. **Creator filter:** momentum plus supplied evidence of at least one earlier launch and at least 50% prior success.
3. **Pre-graduation:** momentum while supplied progress lies between 80% and 95%.

These are configurable starting hypotheses. Creator success and progress must be defined by the recorder and known at signal time. This project does not produce or independently verify those historical features.

### Replay a recording

```bash
PYTHONPATH=src python3 -m pumpfun_lab replay \
  --events data/fixtures/synthetic_market.jsonl \
  --source-label "synthetic engineering fixture" \
  --strategy all --config config/paper.json --output output/replay
```

`--source-label` is required. Real evaluation needs a broad, timestamped recording including coins that failed or became unpriceable. A wallet's selected winning summaries cannot supply every decision-time price and liquidity observation.

Input is one JSON object per line in nondecreasing timestamp order:

```json
{"ts_ms":1000,"mint":"SYNTH_EXAMPLE","price_sol":0.000001,"liquidity_sol":5,"sellable":true,"creator_prior_launches":4,"creator_prior_success_rate":0.5,"progress":0.85}
```

Required fields: integer `ts_ms`, nonempty `mint`, finite positive `price_sol`. Optional features must describe information known at that moment. Events are validated, not silently reordered.

### Execution assumptions

Defaults in `config/paper.json`: 10 SOL virtual cash; 0.05 SOL all-in buys; at most three positions; 1,000 ms delay; 5,000 ms quote/signal age; 125 basis points assumed protocol fee per side; 100 basis points adverse slippage; 0.00001 SOL assumed network cost per filled side; 15% stop-loss, 25% take-profit and 60-second holding-time triggers.

These are modeling settings, not verified live costs. Fees vary by venue, asset, tier and provider. Priority fees, tips, rent, transfer fees and failed transactions need calibration. The model does not reproduce exact reserves, MEV, partial fills, depth depletion or infrastructure outages.

A signal fills only on a later event for the same mint after the delay. Pending buys reserve cash and capacity. A stop or timeout creates an exit attempt, not a guaranteed sale. Stale, unsellable, insufficiently liquid or uneconomic holdings receive zero conservative marks. End-of-file never invents liquidation. Closed-trade PnL, cash movement and conservative portfolio PnL are separate.

Supplied liquidity caps each fill/valuation independently; shared liquidity is not depleted. Missing liquidity imposes no cap, and missing sellability defaults to true. Those assumptions do not prove execution. Rejected simulated attempts incur no modeled network fees.

## Analyze private data locally

Use ignored `data/private/` for personal exports. There is no saved wallet identity.

```bash
PYTHONPATH=src python3 -m pumpfun_lab snapshot \
  --profile data/private/profile_snapshot.json \
  --trades data/private/top_trades.csv \
  --created data/private/created_coins.csv --output output/private-snapshot
```

The synthetic fixtures illustrate input schemas. Set source/provenance fields truthfully when importing real data. Preserve unknown dates, rounded values and repeated tickers as distinct mint identities. An aggregate-minus-winners residual is not automatically a measured loss total.

### Read-only wallet sample

```bash
PYTHONPATH=src python3 -m pumpfun_lab wallet \
  --address YOUR_PUBLIC_SOLANA_ADDRESS \
  --max-signatures 100 --max-transactions 20 --output output/wallet
```

The address is mandatory. Only `getSignaturesForAddress` and `getTransaction` can be called. The public RPC is the default; `SOLANA_RPC_URL` optionally selects an existing provider. Blank values use the public default. No paid provider is configured. Provider usage may follow its own charges/limits; keep credential-bearing URLs in local environment variables.

The importer retains failures, unavailable transactions, exact token amounts and unknown owners. Native SOL changes already include fees paid from that account; wrapped SOL remains separate. Transfers, rewards, rent, creator activity and open inventory need accounting before measuring profit. No USD conversion or realized-PnL claim is produced.

See [wallet data and completeness](docs/wallet-data.md). Raw reports belong in local/private storage. Public CI does not run this importer or upload wallet records.

## GitHub Actions

**Paper research checks** runs on push, pull request and manual invocation. It executes tests, generates the synthetic demo, adds a job summary and retains downloadable JSON/Markdown/HTML for seven days. It uses read-only repository permissions and a five-minute timeout.

**Live market sample** is a separate, bounded collector. Collector changes on main trigger a 90-second free discovery check. Manual runs can request up to 900 seconds. Metered trade data requires both the `PUMPPORTAL_API_KEY` secret and explicit acceptance of charges for that run. It never submits trades. The reports retain sanitized market events for seven days; no personal wallet profile is queried.

Official Actions are pinned to v7 commit hashes verified on 2026-10-27. Both workflows use Python 3.12; only the live workflow installs the optional transport. No recurring live session or automatic paid service is enabled. Inspect the repository's **Actions** tab for actual hosted conclusions and logs. GitHub Actions is not persistent bot hosting.

## Evaluation path

1. Reconcile a complete ledger with opening cost basis, transfers, fees and creator activity.
2. Record all eligible launches with event and arrival times, including failed and illiquid coins.
3. Separate creator-owned activity from external entries. Split development and untouched evaluation periods chronologically.
4. Freeze a baseline and compare one change at a time under common costs, latency, coverage and capital limits.
5. Report net PnL, drawdown, loss tails, concentration, rejected entries and unresolved holdings. Optional AI decisions can be compared later; no model service is required now.

## Primary references

- [Official Pump.fun program documentation](https://github.com/pump-fun/pump-public-docs)
- [Pump.fun fees](https://pump.fun/docs/fees)
- [Solana signature method](https://solana.com/docs/rpc/http/getsignaturesforaddress)
- [Solana transaction method](https://solana.com/docs/rpc/http/gettransaction)
- [Solana JSON structures](https://solana.com/docs/rpc/json-structures)
- [GitHub Actions limits](https://docs.github.com/en/actions/reference/limits)

The repository contains original research tooling. It does not include executable third-party trading-bot code or evidence of verified live profitability.
