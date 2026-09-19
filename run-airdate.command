#!/bin/zsh
# Start airdate from this folder and open it in your browser. Double-click in
# Finder, or run ./run-airdate.command. Stop it with Control-C.
#
# The Obsidian connector owns the Substack sign-in; this launcher never reads
# or passes a Substack cookie.
set -eu

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

# Optional operator overrides (port, auth). Settings live in
# .airdate-data/config.json and are edited in the app.
if [ -f .env.local ]; then
  set -a
  source .env.local
  set +a
fi

export AIR_DATE_PORT="${AIR_DATE_PORT:-8787}"
export PORT="$AIR_DATE_PORT"
URL="http://127.0.0.1:$PORT/airdate"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found. Install Python 3.10 or newer, then run scripts/setup."
  exit 1
fi

# If something already holds the port: reuse airdate, but never stop another program.
if lsof -ti tcp:"$PORT" >/dev/null 2>&1; then
  if curl -fsS "http://127.0.0.1:$PORT/healthz" 2>/dev/null | grep -q '"auth_required"'; then
    echo "airdate is already running at $URL"
    open "$URL" 2>/dev/null || true
    exit 0
  fi
  echo "Port $PORT is in use by another program:"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
  echo "Stop it, or start airdate on another port: AIR_DATE_PORT=8788 ./run-airdate.command"
  exit 1
fi

echo "Starting airdate at $URL"
( sleep 1.5; open "$URL" 2>/dev/null || true ) &
exec python3 server.py
