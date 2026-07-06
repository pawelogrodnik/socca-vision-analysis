#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

cd "$ROOT_DIR/client"

if [ -f "$ROOT_DIR/client/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/client/.env"
  set +a
fi

if command -v npm >/dev/null 2>&1; then
  exec npm run dev -- --host "${CLIENT_HOST:-127.0.0.1}" --port "${CLIENT_PORT:-5173}"
fi

NODE_BIN="${NODE_BIN:-}"
if [ -z "$NODE_BIN" ] && command -v node >/dev/null 2>&1; then
  NODE_BIN="$(command -v node)"
fi
if [ -z "$NODE_BIN" ] && [ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node" ]; then
  NODE_BIN="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
fi
if [ -z "$NODE_BIN" ] || [ ! -x "$NODE_BIN" ]; then
  echo "Node.js was not found. Install Node.js or set NODE_BIN to a node executable." >&2
  exit 1
fi
if [ ! -f "$ROOT_DIR/client/node_modules/vite/bin/vite.js" ]; then
  echo "client/node_modules is missing Vite. Run npm install in client/ first." >&2
  exit 1
fi

exec "$NODE_BIN" "$ROOT_DIR/client/node_modules/vite/bin/vite.js" --host "${CLIENT_HOST:-127.0.0.1}" --port "${CLIENT_PORT:-5173}"
