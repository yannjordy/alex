#!/usr/bin/env bash
set -euo pipefail

export ALEX_LOCAL_MODEL="${ALEX_LOCAL_MODEL:-qwen27b-cyber}"
export ALEX_BROWSER="${ALEX_BROWSER:-thorium-browser}"
export ALEX_COLIBRI_MODEL="${ALEX_COLIBRI_MODEL:-qwen3.6-colibri}"

cd /home/jordy/Projects/alex/alex-prioritaire

# S'assurer qu'Ollama tourne
if ! curl -sf http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "Lancement d'Ollama..."
  ollama serve &
  for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

# S'assurer qu'opencode serve tourne (cerveau principal d'Alex)
if ! curl -sf http://127.0.0.1:4199/api/health > /dev/null 2>&1; then
  echo "Lancement d'opencode serve..."
  setsid nohup opencode serve --port 4199 > /tmp/opencode-serve.log 2>&1 < /dev/null &
  for i in $(seq 1 20); do
    if curl -sf http://127.0.0.1:4199/api/health > /dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

# Tuer l'ancien cerveau
kill $(lsof -ti:8765) 2>/dev/null || true
sleep 1

# Démarrer le cerveau Python
PY="brain/.venv/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3)"
"$PY" -m uvicorn brain.main:app --host 127.0.0.1 --port 8765 &
BRAIN_PID=$!

# Attendre que le cerveau soit prêt
for i in $(seq 1 20); do
  if curl -sf http://127.0.0.1:8765/health > /dev/null 2>&1; then
    break
  fi
  sleep 0.3
done

# Lancer l'interface Electron (CDP sur 9226 pour le pilotage/tests à distance)
exec ./node_modules/.bin/electron --no-sandbox --in-process-gpu --remote-debugging-port=9226 --remote-allow-origins=* .
