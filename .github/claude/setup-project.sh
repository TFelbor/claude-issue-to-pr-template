#!/usr/bin/env bash
#
# Bring the checkout up to the state the project's tests need, before the model runs.
#
# Called by both jobs of .github/workflows/claude-implement.yml. When the repository
# variable SETUP_COMMAND is set it is run as written and nothing else happens; that is
# the supported way to configure this. Without it, the script recognises the two most
# common layouts and otherwise does nothing, loudly.
#
# SETUP_COMMAND is set by a repository admin (Settings -> Secrets and variables ->
# Actions -> Variables), so it is trusted configuration, not pull-request content.

set -euo pipefail

if [ -n "${SETUP_COMMAND:-}" ]; then
  echo "setup: running SETUP_COMMAND"
  bash -euo pipefail -c "$SETUP_COMMAND"
  exit 0
fi

ran=no

if [ -f package-lock.json ] && command -v npm >/dev/null 2>&1; then
  echo "setup: package-lock.json found; npm ci"
  npm ci
  ran=yes
fi

if [ -f requirements.txt ]; then
  py=""
  for candidate in python python3; do
    if command -v "$candidate" >/dev/null 2>&1; then py="$candidate"; break; fi
  done
  if [ -n "$py" ]; then
    echo "setup: requirements.txt found; $py -m pip install -r requirements.txt"
    "$py" -m pip install --upgrade pip
    "$py" -m pip install -r requirements.txt
    ran=yes
  fi
fi

if [ "$ran" = no ]; then
  echo "::notice::setup: no SETUP_COMMAND and no recognised layout; the model starts from a bare checkout. Set the SETUP_COMMAND repository variable (docs/PIPELINE.md § Setup)."
fi
