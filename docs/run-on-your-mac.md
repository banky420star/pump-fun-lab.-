# Run a paper session on your Mac

The Mac starter runs the existing recorder and paper engine on your own computer. It opens one bounded session and saves its report locally. It does not place real orders, run a background trading service, or resume a previous virtual account.

## Start

1. Download and unzip the [current project](https://github.com/banky420star/pump-fun-lab.-/archive/refs/heads/main.zip). Keep the whole extracted folder together.
2. Open **Start-Pumpfun-Lab.command** in that folder. It uses Python 3.11 or newer, creates a local `.venv`, and installs the pinned live-feed transport when needed. If Python is missing, the launcher shows the official [Python macOS download page](https://www.python.org/downloads/macos/).
3. Choose **1** for free discovery, **2** for a paper test using metered trade data, or **q** to quit. Pressing Return chooses free discovery.
4. In trade-data mode, read the fee description, confirm **YES** for that session, and enter the API key for your chosen PumpPortal wallet in the hidden prompt. The application never asks for a wallet private key. Pasted characters will not appear in the terminal.
5. Leave the terminal open during collection. Press **Control-C** to stop. When a report is available, the launcher opens it in your browser and prints its location.

If you prefer Terminal, open Terminal in the extracted project folder and run:

```bash
bash Start-Pumpfun-Lab.command
```

The `.command` launcher uses macOS `caffeinate -i` while the Python session runs. Keep the computer connected to power, online, and the laptop lid open. It does not install a login agent or change your permanent sleep settings. See [Apple's sleep settings guide](https://support.apple.com/guide/mac-help/set-sleep-and-wake-settings-mchle41a6ccd/mac) if the computer still sleeps.

## Session limits and credentials

| Setting | Local starter behavior |
| --- | --- |
| Discovery default | 90 seconds; no API key or metered subscriptions |
| Trade-data default | 300 seconds; explicit charge acceptance |
| Duration range | 1–900 seconds |
| Local message limit | 1,000 processed messages |
| Tracked tokens | First 10 eligible new tokens |
| Trading | Hypothetical paper execution after collection |
| Starting virtual account | Fresh on every run, using `config/paper.json` |
| Results | A separate folder under `output/local/` for every run |
| API-key retention | Requested again for each metered session; never written to a file |

The session stops at its duration or message limit, whichever comes first. Each run is independent. Previous reports remain available; open paper positions do not carry across sessions. Leaving the computer on does not turn this starter into a continuous trading service.

As documented by PumpPortal on 27 October 2026, continuing trade data needs a linked wallet with at least **0.02 SOL**, and costs **0.01 SOL per 10,000 streamed trade events**. The local message limit is not a guaranteed billing cap because provider-sent or queued messages can count toward charges. The API key can also authorize transactions outside this application, so keep it private. The launcher keeps it out of command arguments, source files and saved reports. Python's [getpass](https://docs.python.org/3/library/getpass.html) handles hidden entry; if hidden input is unavailable, the launcher stops instead of echoing the key.

The key selected at the local prompt determines the linked data wallet. No personal wallet identity or credential is built into this public project. The program never needs a wallet private key. If a private key has been exposed, replacing only an API key does not secure the same wallet.

## If a session cannot start

| Result | Next check |
| --- | --- |
| Python requirement message | Install Python 3.11 or newer, then reopen the launcher |
| Dependency installation fails | Check internet access; reopen the launcher after correcting the installation error |
| Hidden input is unavailable | Open the launcher in Terminal so the key can be entered without echo |
| Provider rejects trade subscriptions | Check the selected API key and the linked wallet's data-funding requirement on PumpPortal |
| Network connection fails | Check the network; inspect the saved partial report before rerunning |
| Discovery succeeds but has no paper trades | Discovery has no continuing trade-price series; use trade-data mode to test the paper strategy |

See [live market setup and evidence limits](live-market.md) for the provider's sources, data completeness, indicative-price assumptions, paid-data requirements and the remaining work for continuous operation. A successful connection or positive paper result does not establish real-world profitability.
