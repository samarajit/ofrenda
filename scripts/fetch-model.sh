#!/usr/bin/env bash
# Fetch a GGUF model for Ofrenda.
#
#   scripts/fetch-model.sh                  Qwen3 1.7B, about 1.1 GB
#   scripts/fetch-model.sh qwen3-0.6b       the small one, about 0.6 GB
#   scripts/fetch-model.sh qwen3-4b         if you have the memory to spare
#
# Any GGUF file works. If you already have one, copy it into ./models and
# skip this script entirely.

set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p models

pick="${1:-qwen3-1.7b}"

case "$pick" in
  qwen3-1.7b)
    url="https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf"
    name="Qwen3-1.7B-Q4_K_M.gguf"
    ;;
  qwen3-0.6b)
    url="https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf"
    name="Qwen3-0.6B-Q8_0.gguf"
    ;;
  qwen3-4b)
    url="https://huggingface.co/unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"
    name="Qwen3-4B-Q4_K_M.gguf"
    ;;
  *)
    echo "unknown model: $pick" >&2
    echo "try one of: qwen3-0.6b, qwen3-1.7b, qwen3-4b" >&2
    exit 1
    ;;
esac

if [ -f "models/$name" ]; then
  echo "models/$name is already here."
  exit 0
fi

echo "fetching $name"
echo "from $url"
curl -L --progress-bar -o "models/$name.part" "$url"
mv "models/$name.part" "models/$name"

size=$(du -h "models/$name" | cut -f1)
echo
echo "done. $size in models/$name"
echo "next: python3 ofrenda.py doctor"
