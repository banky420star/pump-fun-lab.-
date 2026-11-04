#!/bin/sh
# Evidence-only inspection of an existing Mac research installation.
# No network trades, Ollama prompts, wallet access, or service restarts.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT="$ROOT/output/mac-recovery-audit.json"
mkdir -p "$ROOT/output"
exec python3 "$ROOT/tools/mac_runtime_audit.py" \
  --project "$HOME/Projects/grok-pumpfun-cloud-recovered" \
  --openbot-db "$HOME/Library/Application Support/OpenBot/openbot.db" \
  --check-dashboard \
  --output "$OUT"
