# Paper Risk Screening v2 — experimental, not wired into execution

This branch adds a **standalone**, deterministic and read-only concentration gate.
It does **not** change existing replay strategies, defaults, collectors or CI workflow
configuration. The existing paper engine must be integrated and tested separately.

## Example

```python
from pumpfun_lab.risk_screen import screen_token
decision = screen_token(
    {"top10_holder_fraction": "0.18", "developer_holder_fraction": "0.02"},
    equity_sol="10",
)
assert decision.eligible
assert str(decision.position_sol) == "0.20"
```

- Top-ten holder concentration cap: **25%**.
- Developer beneficial ownership cap: **5%**.
- Paper position allocation: **2% of current virtual equity**.
- Missing or malformed evidence is rejected, never assumed safe.
- Input fractions are decimals between 0 and 1, **not** percent points.
- Ownership must be measured at decision time, using mint identities and
  documented beneficial-owner clustering. The module does not infer ownership.
- A 35% simulated stop-loss remains a *proposed* separate replay-engine change;
  it is not a guaranteed fill or implemented by this module.
- AI shadow experiments and decision journaling remain planned integration work.
- No live wallet keys, signing, transaction submission or funded trading.

Run tests: `PYTHONPATH=src python3 -m unittest discover -s tests -v`.

Before connecting the gate to the replay engine, add provenance, observation
timestamps, concentration source completeness and rejection accounting. Verify
the actual replay entrypoint and protect against look-ahead bias.
