# Mac Grok Pump.fun recovery audit (read-only)

This is a **separate, local-only inspection tool** for the recovered Grok Pump.fun / OpenBot research desk. It does not turn this repository's bounded collector into a continuous trading service, does not place trades, and cannot certify a profitable or executable strategy.

## Run on the Mac

Requirements: Python 3.11+, and the **separately installed** research desk at `~/Projects/grok-pumpfun-cloud-recovered`. The tool only uses Python's standard library. On the Mac, from this repository:

```bash
bash Start-Runtime-Audit.command
```

Or explicitly select the project and DB:

```bash
python3 tools/mac_runtime_audit.py \
  --project "$HOME/Projects/grok-pumpfun-cloud-recovered" \
  --openbot-db "$HOME/Library/Application Support/OpenBot/openbot.db" \
  --check-dashboard \
  --output output/mac-recovery-audit.json
```

The command **does not install packages, start or stop services, refresh models, change permissions, open wallets, or request keys**. It requests only `http://127.0.0.1:8772/api/operations` to check the local read-only dashboard. It accesses the OpenBot SQLite database with `mode=ro` and `query_only=ON`. It only writes `output/mac-recovery-audit.json` when explicitly asked to export.

Exit code `0` indicates that the audit's engineering checks are satisfied **at that instant**, not financial approval. Exit code `2` means required evidence is missing, unsafe, incomplete, or unavailable. Run without `--check-dashboard` for fully offline inspection; in that case the full engineering check intentionally cannot be declared complete.

## Checks and interpretations

| Check | Required evidence | What it **does not** prove |
| --- | --- | --- |
| Source and paper dataset | Both recovered manifests exist | Manifest presence does not verify all file hashes or signatures |
| Native team | Exactly 13 roles on a 16-node linear workflow, 15 text edges, correct handoff order | Models may still return incorrect or incomplete answers |
| Agent security | All 13 agents restricted to workspace only; computer use disabled | It does not audit all OS permissions or network egress |
| Local Ollama mapping | 13 profiles and all 14 workflow stages use the expected model names (Qwen3.5 4B / 9B and Gemma4 12B MLX) | Model names alone do not prove inference reliability |
| Frozen native run | SHA-256 source fingerprint, exact per-stage entry/exit counters, matching next-role labels, Review BLOCK and final Boss BLOCKED | Doesn't prove trades could execute at reported prices |
| Live paper scanner | Signed-off **paper-only mode**, fresh scanner state, connected feeds, recent fast quote polling | A third-party quote is not independently verified on-chain liquidity |
| Two-hour observation | Complete terminal trial and **same-run** independent 10/10 verdict | Does not establish future profit or authorization to risk capital |
| Censored history | Previous feed outage remains visible | Previous active seconds cannot be combined with a new observation |
| Readiness | Real-money status remains `BLOCK_REAL_MONEY` | The script cannot enable wallet signing |

The audit reports factual status only, using local timestamps and read-only snapshots. A `PASS` for an individual engineering check must not be interpreted as permission to bypass Review or deploy live trades.

## The remaining machine-specific work

The recovered desk must be online to confirm the latest native agent run, verify current model inference (not just installed files), complete a valid **7,200-second contiguous observation**, gather at least **50 distinct independently marked closed tokens**, reconcile costs and adverse liquidity, and prove all timestamped strategy metrics on a held-out period. Previously censored trials remain censored. Any missing executable-depth evidence keeps readiness blocked.

Original Grok-cloud-to-Mac telemetry must be authenticated, fresh, and read-only to count as connected. A static archive, one-time copy, or unauthenticated heartbeat cannot meet this requirement. UI pixel-level parity cannot be certified without access to original pages.

## Automated tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`tests/test_mac_runtime_audit.py` uses synthetic SQLite fixtures and synthetic JSON snapshots only. It covers graph shape, models, permissions, frozen evidence, handoff mismatches, stale feeds, run censoring, numeric safety and missing devices. It does not access live wallets, markets, or the user's local OpenBot database during GitHub Actions.

## Security notes

- Do not commit any private key, API key, provider URL with credentials, raw OpenBot conversation logs, wallet identity, or local runtime database.
- Keep the recovered source and current paper positions on the Mac; the tool intentionally emits summary counts only.
- Leave the native workflow run that failed or misrouted **in its original failed state**; never rewrite historical model outputs to make a checklist pass.
- Never combine separate censored observations or invent successful fills from market marks.
- This audit and the GitHub CI tests do not authorize real-money execution, even if every software test passes.
