#!/usr/bin/env bash
# Put a llama.cpp server in ./vendor/llama.cpp without compiling anything.
#
# It reads the prebuilt release for this machine from the llama.cpp GitHub
# releases. On a machine without a prebuilt release, build llama.cpp yourself
# (cmake -B build && cmake --build build --config Release) and put
# llama-server on your PATH, or set OFRENDA_LLAMA_SERVER to its full path.

set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p vendor

os="$(uname -s)"
arch="$(uname -m)"

case "$os-$arch" in
  Linux-x86_64)  pattern="ubuntu-x64" ;;
  Linux-aarch64) pattern="ubuntu-arm64" ;;
  Darwin-arm64)  pattern="macos-arm64" ;;
  Darwin-x86_64) pattern="macos-x64" ;;
  *)
    echo "no prebuilt runtime for $os $arch." >&2
    echo "build llama.cpp and put llama-server on your PATH." >&2
    exit 1
    ;;
esac

api="https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"

echo "looking up the latest llama.cpp release"
json="$(curl -fsSL "$api")"
tag="$(printf '%s' "$json" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
asset_url="$(printf '%s' "$json" \
  | grep -o '"browser_download_url": *"[^"]*"' \
  | sed 's/.*"\(http[^"]*\)"/\1/' \
  | grep -- "$pattern" \
  | grep 'llama-' \
  | head -1)"

if [ -z "$asset_url" ]; then
  echo "no asset matching $pattern in $tag" >&2
  exit 1
fi

echo "downloading $(basename "$asset_url")"
tmp="$(mktemp -d)"
curl -fL --progress-bar -o "$tmp/runtime.tar.gz" "$asset_url"
tar -xzf "$tmp/runtime.tar.gz" -C "$tmp"
rm -rf vendor/llama.cpp
mkdir -p vendor/llama.cpp
# the archive holds a single directory, usually named after the release
inner="$(find "$tmp" -maxdepth 1 -mindepth 1 -type d | head -1)"
cp -a "$inner"/. vendor/llama.cpp/
chmod +x vendor/llama.cpp/llama-server || true
rm -rf "$tmp"

echo
echo "done. $(vendor/llama.cpp/llama-server --version 2>/dev/null | head -1)"
echo "next: python3 ofrenda.py doctor"
