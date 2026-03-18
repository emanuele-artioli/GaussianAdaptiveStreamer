#!/usr/bin/env bash
set -euo pipefail

DEFAULT_SPKI_HASH="BSQJ0jkQ7wwhR7KvPZ+DSNk2XTZ/MS6xCbo9qu++VdQ="
HOSTPORT="${HOSTPORT:-localhost:4433}"
SPKI_HASH="${SPKI_HASH:-$DEFAULT_SPKI_HASH}"
TARGET="${1:-models-ui}"

if [[ "$TARGET" =~ ^https?:// ]]; then
  URL="$TARGET"
else
  TARGET="${TARGET#/}"
  URL="https://${HOSTPORT}/${TARGET}"
fi

CHROME_ARGS=(
  "--enable-experimental-web-platform-features"
  "--ignore-certificate-errors-spki-list=${SPKI_HASH}"
  "--origin-to-force-quic-on=${HOSTPORT}"
  "$URL"
)

print_dry_run() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    local app="${CHROME_APP:-Google Chrome}"
    printf 'open -a %q --args' "$app"
    printf ' %q' "${CHROME_ARGS[@]}"
    printf '\n'
    return 0
  fi

  local chrome_bin="${CHROME_BIN:-google-chrome}"
  printf '%q' "$chrome_bin"
  printf ' %q' "${CHROME_ARGS[@]}"
  printf '\n'
}

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  print_dry_run
  exit 0
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
  if [[ -n "${CHROME_APP:-}" ]]; then
    open -a "${CHROME_APP}" --args "${CHROME_ARGS[@]}"
    exit 0
  fi

  for app in "Google Chrome" "Google Chrome Canary" "Chromium"; do
    if osascript -e "id of app \"$app\"" >/dev/null 2>&1; then
      open -a "$app" --args "${CHROME_ARGS[@]}"
      exit 0
    fi
  done

  echo "Could not find a Chrome-compatible app on macOS." >&2
  echo "Set CHROME_APP to the installed app name, for example: CHROME_APP='Google Chrome'" >&2
  exit 1
fi

if [[ -n "${CHROME_BIN:-}" ]]; then
  "${CHROME_BIN}" "${CHROME_ARGS[@]}"
  exit 0
fi

for bin in google-chrome google-chrome-stable chromium chromium-browser; do
  if command -v "$bin" >/dev/null 2>&1; then
    "$bin" "${CHROME_ARGS[@]}"
    exit 0
  fi
done

echo "Could not find a Chrome/Chromium binary in PATH." >&2
echo "Set CHROME_BIN to a full path, for example: CHROME_BIN=/usr/bin/google-chrome" >&2
exit 1
