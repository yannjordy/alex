#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

source /home/jordy/alex-venv/bin/activate

export PYTHONPATH="$REPO_ROOT/src"
export ALEX_VOICE_DEBUG=${ALEX_VOICE_DEBUG:-0}
python3 -m alex.daemon
