#!/usr/bin/env bash
set -euo pipefail

# Install a Happy CLI profile that routes Claude Code sessions through cliproxyapi.
#
# Usage:
#   ./scripts/setup_happy_profile.sh [--main MODEL] [--opus MODEL] [--fast MODEL]
#
# Defaults: main=opus4.6  opus=M2.5  fast=g3f
# Requires: jq

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MAIN_MODEL="opus4.6"
OPUS_MODEL="M2.5"
FAST_MODEL="g3f"
LB_PORT="8145"

usage() {
  cat <<'EOF'
Usage:
  setup_happy_profile.sh [options]

Options:
  --main <model>   Main model (ANTHROPIC_MODEL, default: opus4.6)
  --opus <model>   Opus-tier model (ANTHROPIC_DEFAULT_OPUS_MODEL, default: M2.5)
  --fast <model>   Fast/haiku model (ANTHROPIC_DEFAULT_HAIKU_MODEL, default: g3f)
  --port <port>    LB port (default: 8145)
  -h, --help       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --main) MAIN_MODEL="$2"; shift 2 ;;
    --opus) OPUS_MODEL="$2"; shift 2 ;;
    --fast) FAST_MODEL="$2"; shift 2 ;;
    --port) LB_PORT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

command -v jq >/dev/null 2>&1 || { echo "Error: jq is required. Install with: brew install jq" >&2; exit 1; }

HAPPY_DIR="${HAPPY_HOME_DIR:-$HOME/.happy}"
SETTINGS="$HAPPY_DIR/settings.json"
PROFILE_NAME="cliproxy"

# Generate a UUID (macOS uuidgen or fallback)
new_uuid() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen | tr '[:upper:]' '[:lower:]'
  else
    python3 -c "import uuid; print(uuid.uuid4())"
  fi
}

mkdir -p "$HAPPY_DIR"

# Build the profile object
PROFILE_ID="$(new_uuid)"
NOW_MS="$(date +%s)000"

PROFILE=$(jq -n \
  --arg id "$PROFILE_ID" \
  --arg name "$PROFILE_NAME" \
  --arg desc "Local cliproxyapi gateway: ${MAIN_MODEL} + ${OPUS_MODEL} + ${FAST_MODEL}" \
  --arg baseUrl "http://127.0.0.1:${LB_PORT}" \
  --arg model "$MAIN_MODEL" \
  --arg opusModel "$OPUS_MODEL" \
  --arg fastModel "$FAST_MODEL" \
  --argjson now "$NOW_MS" \
  '{
    id: $id,
    name: $name,
    description: $desc,
    anthropicConfig: {
      baseUrl: $baseUrl,
      authToken: "dummy-key",
      model: $model
    },
    environmentVariables: [
      { name: "ANTHROPIC_DEFAULT_HAIKU_MODEL", value: $fastModel },
      { name: "ANTHROPIC_DEFAULT_OPUS_MODEL", value: $opusModel },
      { name: "CLAUDE_CODE_SUBAGENT_MODEL", value: $fastModel }
    ],
    compatibility: { claude: true, codex: false, gemini: false },
    isBuiltIn: false,
    version: "1.0.0",
    createdAt: $now,
    updatedAt: $now
  }')

if [[ -f "$SETTINGS" ]]; then
  # Check if a cliproxy profile already exists
  EXISTING_ID=$(jq -r --arg name "$PROFILE_NAME" '.profiles[]? | select(.name == $name) | .id' "$SETTINGS" 2>/dev/null || true)

  if [[ -n "$EXISTING_ID" ]]; then
    echo "Updating existing '$PROFILE_NAME' profile (id: $EXISTING_ID)"
    PROFILE_ID="$EXISTING_ID"
    # Update the profile in place, preserve the original id
    PROFILE=$(echo "$PROFILE" | jq --arg id "$EXISTING_ID" '.id = $id')
    UPDATED=$(jq --arg name "$PROFILE_NAME" --argjson profile "$PROFILE" '
      .profiles = [.profiles[]? | if .name == $name then $profile else . end]
      | .activeProfileId = $profile.id
    ' "$SETTINGS")
  else
    echo "Adding new '$PROFILE_NAME' profile"
    UPDATED=$(jq --argjson profile "$PROFILE" '
      .schemaVersion = 2
      | .profiles = (.profiles // []) + [$profile]
      | .activeProfileId = $profile.id
      | .localEnvironmentVariables = (.localEnvironmentVariables // {})
    ' "$SETTINGS")
  fi
  echo "$UPDATED" > "$SETTINGS"
else
  echo "Creating $SETTINGS with '$PROFILE_NAME' profile"
  jq -n --argjson profile "$PROFILE" '{
    schemaVersion: 2,
    onboardingCompleted: false,
    activeProfileId: $profile.id,
    profiles: [$profile],
    localEnvironmentVariables: {}
  }' > "$SETTINGS"
fi

echo
echo "Done. Profile '$PROFILE_NAME' is now active."
echo
echo "  Main model:  $MAIN_MODEL"
echo "  Opus model:  $OPUS_MODEL"
echo "  Fast model:  $FAST_MODEL"
echo "  LB endpoint: http://127.0.0.1:$LB_PORT"
echo
echo "Start a Happy session with:  happy"
