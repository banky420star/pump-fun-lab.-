#!/bin/bash
# Local, interactive paper sessions. No credential is embedded in this file.
set -euo pipefail
umask 077

LAB_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
cd "$LAB_ROOT"
# The launcher prompts privately later; installers never need this credential.
unset PUMPPORTAL_API_KEY

finish() {
  LAB_EXIT_CODE=$?
  if [ "$LAB_EXIT_CODE" -ne 0 ]; then
    printf '\nSession ended with status %s. The message above explains the result.\n' "$LAB_EXIT_CODE"
  fi
  if [ -t 0 ]; then
    printf '\nPress Return to close this window.'
    IFS= read -r LAB_CLOSE_INPUT || true
  fi
  exit "$LAB_EXIT_CODE"
}
trap finish EXIT

printf '\nPump.fun Lab — local paper testing\n'
printf 'Preparing the local Python environment. No wallet private key is needed.\n\n'

if [ -x "$LAB_ROOT/.venv/bin/python3" ]; then
  LAB_PYTHON="$LAB_ROOT/.venv/bin/python3"
  if ! "$LAB_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    printf 'The existing .venv needs Python 3.11 or newer. Use a fresh project folder with a supported Python.\n' >&2
    exit 2
  fi
else
  LAB_BASE_PYTHON=''
  for LAB_CANDIDATE in python3.12 python3.13 python3.11 python3.14 python3.15 python3; do
    if command -v "$LAB_CANDIDATE" >/dev/null 2>&1 &&
       "$LAB_CANDIDATE" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      LAB_BASE_PYTHON="$(command -v "$LAB_CANDIDATE")"
      break
    fi
  done
  if [ -z "$LAB_BASE_PYTHON" ]; then
    printf 'Install Python 3.11 or newer from https://www.python.org/downloads/macos/ then open this launcher again.\n' >&2
    exit 2
  fi
  "$LAB_BASE_PYTHON" -m venv "$LAB_ROOT/.venv"
  LAB_PYTHON="$LAB_ROOT/.venv/bin/python3"
fi

if ! "$LAB_PYTHON" -c 'import websockets; raise SystemExit(0 if websockets.__version__ == "16.0" else 1)' >/dev/null 2>&1; then
  "$LAB_PYTHON" -m pip install --disable-pip-version-check -r "$LAB_ROOT/requirements-live.txt"
fi

export PYTHONPATH="$LAB_ROOT/src"
if [ "$(uname -s)" = 'Darwin' ] && command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i "$LAB_PYTHON" -m pumpfun_lab.launcher
else
  "$LAB_PYTHON" -m pumpfun_lab.launcher
fi
