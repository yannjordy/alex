#!/bin/bash
while true; do
  /usr/local/bin/ollama pull richardyoung/llama-3.2-3b-instruct-abliterated
  if [ $? -eq 0 ]; then
    echo "Download complete!"
    exit 0
  fi
  echo "Download failed/interrupted, restarting in 5s..."
  sleep 5
done