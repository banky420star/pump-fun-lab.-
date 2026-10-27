# Connect to live Pump.fun market data

This project can collect a bounded live feed through **PumpPortal**, a third-party data provider. Collection does not sign transactions, submit orders, or establish profitability. When trade-price observations are available, a paper replay runs **after** the sample ends.

## Start the free connection check

In GitHub, open **Actions → Live market sample → Run workflow**. Select the main branch, leave mode as **discovery**, and run. No credential or data-wallet funding is used by this mode. Changes to the collector on main also trigger a 90-second free sample automatically.

The job records new-token and migration events. Open its summary and the **live-market-report** artifact to see the actual connection outcome, arrival times, counts, errors and sanitized public market events. A connected socket without market events is not reported as a successful market-data check. Creation events alone produce no strategy PnL.

To run on your own computer, use Python 3.11 or newer from the repository root:

```bash
python3 -m pip install -r requirements-live.txt
PYTHONPATH=src python3 -m pumpfun_lab live \
  --mode discovery --duration-seconds 90 --max-messages 1000 --output output/live
```

Open `output/live/report.html`. The transport is pinned to websockets 16.0; all offline tools still use only the standard library.

## Enable trade observations for paper testing

As checked on 27 October 2026, PumpPortal documents these conditions:

| Data subscription | Provider requirement |
| --- | --- |
| New tokens and migrations | Free |
| Trades for subscribed tokens or accounts | Linked API key, at least 0.02 SOL in its wallet, 0.01 SOL per 10,000 trade events |

1. Open the provider's [wallet and API-key setup](https://pumpportal.fun/trading-api/setup/). Create a separate wallet for this integration and securely retain the recovery material yourself.
2. If you choose to use metered data, fund that separate wallet according to the provider's current requirement and your data-spend budget. This project never initiates the transfer.
3. In the repository, use **Settings → Secrets and variables → Actions → New repository secret**. Set the name to `PUMPPORTAL_API_KEY` and put the API key in its value. Do not paste it into chat, an issue, source code, workflow inputs or a command-line argument.
4. Open **Actions → Live market sample → Run workflow**, select **trades**, choose a bounded duration and message limit, and explicitly check acceptance of data charges. The defaults follow only the first 10 newly seen tokens, for at most 300 seconds and 1,000 locally processed messages.
5. Read the resulting report. No accepted price observations means no strategy evaluation. The artifact distinguishes market evidence from paper execution assumptions and unfilled inventory.

**The provider key can also authorize trades from its linked wallet. It is not a read-only credential.** The application only issues data-subscription messages, but that does not narrow the key's provider-level permissions. Store it as a financial secret and keep the wallet separate from savings or unrelated trading funds.

For local metered runs, set the key in a private environment variable named `PUMPPORTAL_API_KEY`, then:

```bash
PYTHONPATH=src python3 -m pumpfun_lab live \
  --mode trades --accept-data-charges \
  --duration-seconds 300 --max-messages 1000 --max-tokens 10 --output output/live
```

No paid subscription is enabled by merely adding the secret. Automatic push runs always use free discovery and never receive the secret.

## What the sample can tell you

The adapter uses one WebSocket connection. It follows a bounded set of newly observed mints, rejects malformed prices, deduplicates repeated events, and records receive-time order. It stops on a disconnect or provider error and preserves partial evidence. It does not reconnect or fill a data gap from synthetic data.

Accepted trade prices are calculated from finite positive `vSolInBondingCurve / vTokensInBondingCurve` fields. These are **indicative reserve ratios**, not verified executable quotes. The provider's public documentation does not define a versioned payload schema. Unsupported formats and explicit non-SOL quote currencies are excluded. Virtual reserves are not used as cash liquidity. Trade events are processed-commitment observations and are not independently finalized on-chain.

The initial live comparison uses the existing momentum hypothesis. Creator-filter and pre-graduation strategies require historical features this adapter does not supply. Its sample of the first subscribed launches is not a complete market census. New runs start fresh virtual accounts; they do not resume open holdings from another run.

Paper results include the configured hypothetical trading costs, but **exclude actual data and hosting charges**, which the application cannot verify. Message counts are local stop conditions, **not a guaranteed provider billing cap**: messages already sent or queued can affect provider charges. Do not interpret a small positive paper result as net real profit.

Inventory valuation uses the final accepted price-observation time. A quiet period may separate that observation from the end of collection; the report exposes that trailing gap explicitly. It does not claim that the prior valuation could still be realized at capture end.

## Running continuously

This workflow is a finite collection and replay job, with a maximum of 900 seconds per capture. It is not a continuously hosted bot. GitHub-hosted Actions jobs have a six-hour platform limit, and scheduled jobs can be delayed or dropped. There is no recurring paid schedule in this repository.

A continuous paper service needs an always-on computer or server, private credential storage, persistent position and event state, reconnect/recovery handling, monitoring and a defined operating budget. Those are separate from proving the first connection. A funded execution service also needs order routing, sell-route checks, transaction reconciliation, approved capital/loss limits and realistic out-of-sample evidence; none is silently enabled here.

## Sources

- [PumpPortal third-party status](https://pumpportal.fun/)
- [Live streams and connection rules](https://pumpportal.fun/data-api/real-time/)
- [Provider fees](https://pumpportal.fun/fees/)
- [Wallet/API-key capabilities](https://pumpportal.fun/trading-api/setup/)
- [Commitment, limits, historical data and disconnect caveats](https://pumpportal.fun/FAQ/)
- [GitHub Actions limits](https://docs.github.com/en/actions/reference/limits)
- [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
