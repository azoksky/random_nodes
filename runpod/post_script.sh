#!/usr/bin/env bash
set -euo pipefail

# --- Config ---
COMFYUI_PATH="${COMFYUI_PATH:-/workspace/ComfyUI}"
WORKSPACE="$(dirname "$COMFYUI_PATH")"

REQ_URL="https://raw.githubusercontent.com/azoksky/az-nodes/refs/heads/main/other/runpod/requirements.txt"
REQ_DEST="$WORKSPACE/requirements.runpod.txt"

PY_URL="https://raw.githubusercontent.com/azoksky/az-nodes/refs/heads/main/other/runpod/prepare_comfy.py"
PY_DEST="$WORKSPACE/prepare_comfy.py"

# --- Helpers ---
dl() {
  curl -fsSL --retry 5 --retry-delay 2 --proto '=https' --tlsv1.2 "$1" -o "$2"
}

dos2unix_inplace() {
  sed -i 's/\r$//' "$1"
}

# --- Prep ---
mkdir -p "$WORKSPACE"

# --- Get & install requirements ---
echo "Downloading requirements from: $REQ_URL"
dl "$REQ_URL" "$REQ_DEST"
dos2unix_inplace "$REQ_DEST"

echo "Installing Python dependencies..."
python3 -m pip install --no-cache-dir --prefer-binary -r "$REQ_DEST"

# --- Get runner script ---
echo "Downloading runner script from: $PY_URL"
dl "$PY_URL" "$PY_DEST"
dos2unix_inplace "$PY_DEST"
chmod +x "$PY_DEST" || true

# --- Execute ---
if [ "$$" -eq 1 ]; then
  exec python3 -u "$PY_DEST" "$@"
else
  python3 -u "$PY_DEST" "$@"
fi
