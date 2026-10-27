# Wallet data and completeness

The importer accepts an explicit public Solana address. It has no stored wallet identity and cannot sign or submit a transaction.

## Request bounds

`fetch_wallet_history(address, rpc_url=None, max_signatures=100, max_transactions=20, *, transport=None, timeout=10.0)` permits one signature page and up to 20 details. Only `getSignaturesForAddress` and `getTransaction` are allowed. There is no pagination, account discovery or retry. Public RPC access can be rate limited; a custom endpoint's cluster is not independently verified.

The optional `SOLANA_RPC_URL` selects an existing provider. Blank values use the public default. Reports retain only its origin, omitting URL credentials, path, query and fragment. Ordinary transport errors and server error text are sanitized. Responses are capped at 8 MiB and request timeouts at 30 seconds.

## Evidence schema

Returned fields include `address`, `provenance`, `completeness`, `raw`, `rows`, and `errors`. Raw RPC results remain beside normalized observations. Network failures return an evidence report; invalid local configuration raises `ValueError`. The CLI writes reports and returns code 1 for retrieval errors, or 2 for invalid input.

Rows retain signature, slot, time, status, whole-transaction fee, wallet-paid fee, native balance delta, owner-specific token deltas, unresolved ownership, and quality flags. Token amounts use integers/decimal strings. Lookup-table addresses are resolved in order; expanded parsed keys are not appended twice. Missing metadata and entire balance arrays remain unknown.

Native changes already reflect wallet-paid fees, which must not be deducted again. Wrapped SOL remains a separate token observation. Transfers, wraps, ownership changes, rewards, creation costs and rent are not labeled as purchases/sales. Recognized Pump/PumpSwap instruction references establish only reference evidence, not a decoded trade. Outer instructions of a failed transaction may never have executed.

## Coverage

Signature queries cover transactions referencing the supplied address in account keys. Incoming token transfers can reference a token account without its owner's wallet address; historical closed token accounts are not discovered. Missing, malformed, duplicate and unrequested observations have separate counts.

`history_complete` and `pnl_ready` always remain false. `all_observed_signatures_fetched` concerns only the observed page. Unsupported transaction versions remain missing evidence. No USD conversion or realized-PnL calculation is produced.

Complete trading analysis needs period coverage, opening inventory/cost basis, all relevant accounts, deposits/withdrawals, creator-fee claims, disposals and costs. Dollar valuation also needs a time-stamped conversion source.

## Storage

Local results include the queried wallet and raw transactions. Use ignored `output/`, `reports/`, `data/private/`, or a private destination. Public CI uses synthetic data only and does not invoke this importer.

## Primary sources

- [Solana signature method](https://solana.com/docs/rpc/http/getsignaturesforaddress)
- [Solana transaction method](https://solana.com/docs/rpc/http/gettransaction)
- [Solana JSON structures](https://solana.com/docs/rpc/json-structures)
- [Solana public endpoints](https://solana.com/docs/references/clusters)
- [Official Pump program documentation](https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/PUMP_PROGRAM_README.md)
- [Official PumpSwap documentation](https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/PUMP_SWAP_README.md)
