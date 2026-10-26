#!/bin/sh
# ============================================================
# FreeFrame installer: one command on a fresh Ubuntu or Debian server.
#
#   curl -fsSL https://raw.githubusercontent.com/Techiebutler/freeframe/latest/install.sh | sudo bash
#
# This small script downloads the freeframe command from the same branch or
# tag (FREEFRAME_CHANNEL, latest by default), installs it as
# /usr/local/bin/freeframe, and runs `freeframe install` with the terminal
# attached, so its questions work even though this script arrives through a
# pipe. Without a terminal (cloud-init, CI) it runs without one, and every
# answer must be given ahead of time. Options are passed on, for example:
#
#   curl -fsSL .../install.sh | sudo bash -s -- --yes --domain review.yourstudio.com
#
# Everything sits in main(), which runs on the last line, so a download that
# is cut short never runs half a script.
#
# Test-only: FF_BIN_DIR installs the command somewhere else (deploy/tests).
# ============================================================

main() {
  ref=${FREEFRAME_CHANNEL:-latest}
  case $ref in
    "" | -* | *[!A-Za-z0-9._/-]* | *..*)
      echo "FREEFRAME_CHANNEL must be a branch or tag name such as stable or latest (it is '$ref')." >&2
      echo "What to try: leave FREEFRAME_CHANNEL unset, or set it to stable or latest." >&2
      exit 2
      ;;
  esac

  if [ "$(id -u)" != 0 ]; then
    echo "Please run the installer as root." >&2
    echo "What to try: put sudo in front of bash:" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/Techiebutler/freeframe/$ref/install.sh | sudo bash" >&2
    exit 2
  fi

  if ! command -v curl >/dev/null 2>&1; then
    echo "This server has no curl, which the installer needs." >&2
    echo "What to try: sudo apt-get install -y curl, then run the install command again." >&2
    exit 1
  fi

  bin_dir=${FF_BIN_DIR:-/usr/local/bin}
  url="https://raw.githubusercontent.com/Techiebutler/freeframe/$ref/deploy/freeframe"
  tmp=$(mktemp) || exit 1

  echo "Downloading the freeframe command ($ref)..."
  if ! curl -fsSL --retry 3 --max-time 60 "$url" -o "$tmp"; then
    rm -f "$tmp"
    echo "Could not download the freeframe command from $url" >&2
    echo "What to try: check that this server can reach raw.githubusercontent.com, then run the install command again. If it can, the '$ref' channel may not have the installer yet: use FREEFRAME_CHANNEL=latest." >&2
    exit 1
  fi
  if [ "$(head -n 1 "$tmp")" != '#!/usr/bin/env bash' ]; then
    rm -f "$tmp"
    echo "What was downloaded from $url is not the freeframe command." >&2
    echo "What to try: run the install command again in a few minutes. If it keeps happening, the '$ref' channel may not have the installer yet: use FREEFRAME_CHANNEL=latest." >&2
    exit 1
  fi
  if ! { mkdir -p "$bin_dir" && install -m 755 "$tmp" "$bin_dir/freeframe"; }; then
    rm -f "$tmp"
    echo "Could not install the freeframe command into $bin_dir." >&2
    echo "What to try: check that the disk is not full or read-only, then run the install command again." >&2
    exit 1
  fi
  rm -f "$tmp"

  # Under `curl | bash` this script's input is the download, so the
  # questions read the terminal instead, when there is one.
  if (: </dev/tty) 2>/dev/null; then
    exec "$bin_dir/freeframe" install --channel "$ref" "$@" </dev/tty
  fi
  exec "$bin_dir/freeframe" install --channel "$ref" "$@"
}

main "$@"
