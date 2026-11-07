#!/bin/sh
# Join Pablo from a Mac or Linux machine. `pablo invite` prints the exact command:
#   curl -fsSL .../scripts/join.sh | sh -s -- '<tailscale share link>' '<pablo join link>'
# Installs Tailscale if it's missing, signs you in, has you accept the share, waits until
# Pablo answers, then opens your invite link in the browser.
set -eu

SHARE="${1:?usage: join.sh <tailscale share link> <pablo join link>}"
JOIN="${2:?usage: join.sh <tailscale share link> <pablo join link>}"
OS="$(uname -s)"

open_url() {
  if [ "$OS" = Darwin ]; then open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
  else echo "  Open this in your browser: $1"
  fi
}

find_ts() {
  if command -v tailscale >/dev/null 2>&1; then command -v tailscale
  elif [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then
    echo /Applications/Tailscale.app/Contents/MacOS/Tailscale
  fi
}

echo "Joining Pablo"

TS="$(find_ts)"
if [ -z "$TS" ]; then
  case "$OS" in
    Linux)
      echo "- Installing Tailscale (it may ask for your password)"
      curl -fsSL https://tailscale.com/install.sh | sh
      TS="$(find_ts)"
      ;;
    Darwin)
      echo "- Install Tailscale from the page that just opened, open it, and sign in."
      echo "  This keeps going once it's installed."
      open_url https://tailscale.com/download/mac
      while [ -z "$TS" ]; do sleep 3; TS="$(find_ts)"; done
      ;;
    *)
      echo "Install Tailscale from https://tailscale.com/download, then run this again."
      exit 1
      ;;
  esac
fi

if ! "$TS" status >/dev/null 2>&1; then
  echo "- Signing in to Tailscale (finish in the browser)"
  if [ "$OS" = Linux ]; then sudo "$TS" up; else "$TS" up || true; fi
fi

echo "- Accept the Pablo share in the browser tab that opens"
open_url "$SHARE"

HOST="$(printf '%s' "$JOIN" | sed -E 's#^(https?://[^/]+).*#\1#')"
printf -- "- Waiting for Pablo"
tries=0
until curl -fsS --max-time 4 "$HOST/api/health" >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -ge 100 ]; then
    echo
    echo "Couldn't reach Pablo at $HOST after 5 minutes."
    echo "Check that you accepted the share and that Tailscale is connected, then run this again."
    exit 1
  fi
  printf .
  sleep 3
done
echo " found it"

open_url "$JOIN"
echo "You're in. Bookmark $HOST"
