#!/usr/bin/env bash
# Lance Alex depuis le menu d'applications : services si absents + Electron.
cd /home/jordy/Documents/alex || exit 1
export ALEX_LOCAL_MODEL="${ALEX_LOCAL_MODEL:-qwen2.5:7b}"

# opencode serve (si absent)
if ! curl -sf -m 2 http://127.0.0.1:4199/api/health > /dev/null 2>&1; then
  setsid nohup opencode serve --port 4199 > /tmp/opencode-serve.log 2>&1 < /dev/null &
  for i in $(seq 1 25); do curl -sf -m 2 http://127.0.0.1:4199/api/health > /dev/null 2>&1 && break; sleep 1; done
fi

# cerveau Python (seulement s'il est absent — ne jamais tuer l'instance en cours)
if ! curl -sf -m 2 http://127.0.0.1:8765/health > /dev/null 2>&1; then
  setsid nohup brain/.venv/bin/python3 -m uvicorn brain.main:app --host 127.0.0.1 --port 8765 >> /tmp/brain.log 2>&1 < /dev/null &
  for i in $(seq 1 20); do curl -sf -m 2 http://127.0.0.1:8765/health > /dev/null 2>&1 && break; sleep 0.5; done
fi

# Electron — instance unique : si déjà ouvert, il reçoit le focus puis quitte.
exec ./node_modules/.bin/electron --no-sandbox --in-process-gpu . >> /tmp/electron-alex.log 2>&1
