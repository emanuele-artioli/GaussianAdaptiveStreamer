#!/usr/bin/env bash
set -euo pipefail

DEFAULT_SPKI_HASH="BSQJ0jkQ7wwhR7KvPZ+DSNk2XTZ/MS6xCbo9qu++VdQ="
HOSTPORT="${HOSTPORT:-localhost:4433}"
SPKI_HASH="${SPKI_HASH:-$DEFAULT_SPKI_HASH}"
TARGET="${1:-models-ui}"
OS_NAME="$(uname -s)"

CHROME_FLAGS=(
  "--new-window"
  "--enable-experimental-web-platform-features"
  "--ignore-certificate-errors-spki-list=${SPKI_HASH}"
  "--origin-to-force-quic-on=${HOSTPORT}"
)

if [[ "$TARGET" =~ ^https?:// ]]; then
  URL="$TARGET"
else
  TARGET="${TARGET#/}"
  URL="https://${HOSTPORT}/${TARGET}"
fi


resolve_macos_chrome_bin() {
  local spec="${CHROME_APP:-}"
  local bundle=""

  if [[ -n "${CHROME_BIN:-}" ]]; then
    if [[ -x "${CHROME_BIN}" ]]; then
      printf '%s\n' "${CHROME_BIN}"
      return 0
    fi
    echo "CHROME_BIN is set but not executable: ${CHROME_BIN}" >&2
    return 1
  fi

  local candidates=()
  if [[ -n "$spec" ]]; then
    candidates+=("$spec")
  else
    candidates+=("Google Chrome" "Google Chrome Canary" "Chromium")
  fi

  local c
  for c in "${candidates[@]}"; do
    if [[ -x "$c" && "$c" != *.app ]]; then
      printf '%s\n' "$c"
      return 0
    fi

    bundle=""
    if [[ "$c" == *.app && -d "${c%/}" ]]; then
      bundle="${c%/}"
    elif [[ "$c" == *.app && -d "/Applications/${c}" ]]; then
      bundle="/Applications/${c}"
    elif [[ "$c" == *.app && -d "$HOME/Applications/${c}" ]]; then
      bundle="$HOME/Applications/${c}"
    elif [[ -d "/Applications/${c}.app" ]]; then
      bundle="/Applications/${c}.app"
    elif [[ -d "$HOME/Applications/${c}.app" ]]; then
      bundle="$HOME/Applications/${c}.app"
    fi

    if [[ -n "$bundle" ]]; then
      local bin_name
      local bin_path
      bin_name="$(basename "${bundle%.app}")"
      bin_path="$bundle/Contents/MacOS/$bin_name"
      if [[ -x "$bin_path" ]]; then
        printf '%s\n' "$bin_path"
        return 0
      fi
    fi
  done

  return 1
}


print_dry_run() {
  if [[ "$OS_NAME" == "Darwin" ]]; then
    local profile_dir="${CHROME_USER_DATA_DIR:-/tmp/gaussian-streamer-quic-profile}"
    local chrome_bin_hint="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
    printf '%q' "$chrome_bin_hint"
    printf ' --user-data-dir=%q' "$profile_dir"
    printf ' %q' "${CHROME_FLAGS[@]}"
    printf ' %q\n' "$URL"
    return 0
  fi

  local chrome_bin="${CHROME_BIN:-google-chrome}"
  printf '%q' "$chrome_bin"
  printf ' %q' "${CHROME_FLAGS[@]}"
  printf ' %q' "$URL"
  printf '\n'
}

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  print_dry_run
  exit 0
fi

if [[ "$OS_NAME" == "Darwin" ]]; then
  CHROME_BIN_PATH="$(resolve_macos_chrome_bin || true)"
  if [[ -z "$CHROME_BIN_PATH" ]]; then
    echo "Could not find a Chrome-compatible app on macOS." >&2
    echo "Set CHROME_BIN to the executable path or CHROME_APP to an installed app name." >&2
    exit 1
  fi

  PROFILE_DIR="${CHROME_USER_DATA_DIR:-/tmp/gaussian-streamer-quic-profile}"
  mkdir -p "$PROFILE_DIR"

  "$CHROME_BIN_PATH" \
    "--user-data-dir=$PROFILE_DIR" \
    "${CHROME_FLAGS[@]}" \
    "$URL" >/dev/null 2>&1 &

  disown || true
  exit 0
fi

if [[ -n "${CHROME_BIN:-}" ]]; then
  "${CHROME_BIN}" "${CHROME_FLAGS[@]}" "$URL"
  exit 0
fi

for bin in google-chrome google-chrome-stable chromium chromium-browser; do
  if command -v "$bin" >/dev/null 2>&1; then
    "$bin" "${CHROME_FLAGS[@]}" "$URL"
    exit 0
  fi
done

echo "Could not find a Chrome/Chromium binary in PATH." >&2
echo "Set CHROME_BIN to a full path, for example: CHROME_BIN=/usr/bin/google-chrome" >&2
exit 1
