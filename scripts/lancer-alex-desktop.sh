#!/usr/bin/env bash
set -euo pipefail
source /home/jordy/alex-venv/bin/activate
cd /home/jordy/alex
export PYTHONPATH="$PWD/src"
exec python -m desktop_app "$@"
