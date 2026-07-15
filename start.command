#!/bin/bash
# Maison Monitor — one-click launcher (macOS: double-click this file).
# Installs everything it needs on first run, then opens the Console in your
# browser. Safe to run repeatedly.
set -e
cd "$(dirname "$0")"

echo "──────────────────────────────────────────"
echo "  MAISON MONITOR"
echo "──────────────────────────────────────────"

# 1. uv (Python package manager) — install once if missing
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "→ First-time setup: installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. project dependencies (fast when already installed)
echo "→ Checking dependencies…"
uv sync --quiet

# 3. browser engine for post screenshots (no-op when already present)
uv run playwright install chromium

# 4. one-time key check — GitHub blocks committing Anthropic keys, so the
#    first person to run this pastes it once; it's saved to .env locally
while ! uv run python -c "from mm.config import Settings; Settings.load()" >/dev/null 2>&1; do
  echo ""
  echo "One-time setup: paste the ANTHROPIC_API_KEY (ask Albert / see the"
  echo "team password manager), then press Enter:"
  read -r KEY
  if [ -n "$KEY" ]; then
    touch .env
    printf '\nANTHROPIC_API_KEY=%s\n' "$KEY" >> .env
  fi
done

# 5. launch the Console (leave this window open; Ctrl-C to stop)
echo "→ Starting the Console — your browser will open shortly…"
exec uv run mm console
