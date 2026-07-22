#!/usr/bin/env bash
set -euo pipefail
MODEL="richardyoung/llama-3.2-3b-instruct-abliterated"
LOG="/tmp/ollama-pull.log"
while true; do
  echo "[$(date)] Pulling $MODEL..."
  ollama pull "$MODEL" >> "$LOG" 2>&1
  EXIT_CODE=$?
  if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date)] Telechargement termine !"
    exit 0
  fi
  echo "[$(date)] Echec (code $EXIT_CODE), reprise dans 10s..."
  sleep 10
done
