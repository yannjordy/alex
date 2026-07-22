#!/bin/bash
# Attend que internet revienne, puis lance le telechargement avec aria2c
URL="https://huggingface.co/Hasaranga85/Llama-3.2-3B-Instruct-abliterated-Q4_K_M-GGUF/resolve/main/llama-3.2-3b-instruct-abliterated-q4_k_m.gguf"
OUT="/mnt/models/ollama-models/blobs/llama-3.2-3b-abliterated-q4_k_m.gguf"
LOG="/tmp/aria2c.log"

echo "$(date) | Attente internet..." >> "$LOG"
while ! curl -s --max-time 5 https://huggingface.co >/dev/null 2>&1; do
  sleep 30
done
echo "$(date) | Internet OK, lancement aria2c..." >> "$LOG"

while true; do
  aria2c -c -x4 -s4 --timeout=120 --max-tries=0 --retry-wait=30 \
    --dir=/mnt/models/ollama-models/blobs/ \
    --out=llama-3.2-3b-abliterated-q4_k_m.gguf \
    "$URL" >> "$LOG" 2>&1
  
  # verifier si le fichier est complet (~2 GB)
  SIZE=$(stat -f%z "$OUT" 2>/dev/null || stat -c%s "$OUT" 2>/dev/null)
  if [ "$SIZE" -gt 1900000000 ] 2>/dev/null; then
    echo "$(date) | TELECHARGEMENT TERMINE! $OUT ($SIZE bytes)" >> "$LOG"
    break
  fi
  
  echo "$(date) | Reconnexion dans 30s..." >> "$LOG"
  sleep 30
done