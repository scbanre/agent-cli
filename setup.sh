#!/usr/bin/env bash
set -euo pipefail

# agent-cli one-click setup script
# Run from the repo root after `git clone --recursive`

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ---------- helpers ----------
info()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m[WARN] %s\033[0m\n' "$*"; }
err()   { printf '\033[1;31m[ERROR] %s\033[0m\n' "$*"; exit 1; }

# ---------- 1. check required dependencies ----------
info "Checking dependencies"

for cmd in python3 node npm; do
  command -v "$cmd" >/dev/null 2>&1 || err "'$cmd' not found. Please install it first."
done

if ! command -v go >/dev/null 2>&1; then
  warn "'go' not found. You won't be able to build cliproxy from source."
  warn "You can install Go later or download a pre-built binary from Releases."
fi

# ---------- 2. init submodule ----------
if [[ ! -f source_code/.git ]] && [[ ! -d source_code/.git ]]; then
  info "Initializing git submodules"
  git submodule update --init --recursive
else
  info "Submodule already initialized"
fi

# ---------- 3. copy templates ----------
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    warn ".env created from template — please edit it with your API keys."
  else
    warn "No .env.example found; skipping .env creation."
  fi
else
  info ".env already exists"
fi

if [[ ! -f providers.toml ]]; then
  if [[ -f providers.toml.example ]]; then
    cp providers.toml.example providers.toml
    warn "providers.toml created from template — please edit it with your config."
  else
    warn "No providers.toml.example found; skipping providers.toml creation."
  fi
else
  info "providers.toml already exists"
fi

# ---------- 4. npm install ----------
info "Installing Node.js dependencies"
npm install

# ---------- 5. build cliproxy binary (optional) ----------
if [[ ! -f cliproxy ]]; then
  if command -v go >/dev/null 2>&1 && [[ -d source_code/cmd/server ]]; then
    info "Building cliproxy from source_code submodule"
    (cd source_code && go build -o ../cliproxy ./cmd/server/)
  else
    warn "cliproxy binary not found and cannot build (no go or no source)."
    warn "Download a pre-built binary from Releases and place it at: $SCRIPT_DIR/cliproxy"
  fi
else
  info "cliproxy binary already exists"
fi

# ---------- 6. generate config ----------
info "Generating runtime config"
python3 generate_config.py

# ---------- done ----------
echo
info "Setup complete!"
echo
echo "Next steps:"
echo "  1. Edit .env and providers.toml if you haven't already"
echo "  2. Start services:  make start   (or: pm2 start ecosystem.config.js)"
echo "  3. (Optional) OAuth login:"
echo "       ./cliproxy --antigravity-login"
echo "       ./cliproxy --codex-login"
echo "  4. (Optional) Deploy cld to zsh fpath:  ./deploy_cld.sh"
