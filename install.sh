#!/bin/sh
# ============================================================
# FreeFrame installer: one command on a fresh Ubuntu or Debian server.
#
#   curl -fsSL https://raw.githubusercontent.com/Techiebutler/freeframe/latest/install.sh | sudo bash
#
# This small script downloads the freeframe command from the same branch or
# tag and runs `freeframe install` from that download, with the terminal
# attached, so its questions work even though this script arrives through a
# pipe. It writes nothing itself: `freeframe install` puts the command at
# /usr/local/bin/freeframe once its checks have passed and the install goes
# ahead, so a refused run leaves the server as it was.
#
# The branch or tag is --channel when given, otherwise FREEFRAME_CHANNEL,
# otherwise latest. sudo drops variables set before it, so put them after it:
#
#   curl -fsSL .../install.sh | sudo FREEFRAME_CHANNEL=stable bash
#   curl -fsSL .../install.sh | sudo bash -s -- --yes --domain review.yourstudio.com
#
# Without a terminal (cloud-init, CI) it runs without one, and every answer
# must be given ahead of time.
#
# Everything sits in main(), which runs on the last line, so a download that
# is cut short never runs half a script.
# ============================================================

RAW=https://raw.githubusercontent.com/Techiebutler/freeframe

main() {
  ref=${FREEFRAME_CHANNEL:-latest}
  # The script must come from the channel it installs: --channel wins.
  prev=""
  for arg in "$@"; do
    case $arg in
      --channel=*) ref=${arg#--channel=} ;;
      *) [ "$prev" = --channel ] && ref=$arg ;;
    esac
    prev=$arg
  done
  case $ref in
    "" | -* | *[!A-Za-z0-9._/-]* | *..*)
      echo "The channel must be a branch or tag name such as stable or latest (it is '$ref')." >&2
      echo "What to try: leave it out, or use stable or latest, for example:" >&2
      echo "  curl -fsSL $RAW/latest/install.sh | sudo FREEFRAME_CHANNEL=latest bash" >&2
      exit 2
      ;;
  esac

  if [ "$(id -u)" != 0 ]; then
    echo "Please run the installer as root." >&2
    echo "What to try: put sudo in front of bash:" >&2
    echo "  curl -fsSL $RAW/$ref/install.sh | sudo FREEFRAME_CHANNEL=$ref bash" >&2
    exit 2
  fi

  if ! command -v curl >/dev/null 2>&1 || ! command -v bash >/dev/null 2>&1; then
    echo "This server is missing curl or bash, which the installer needs." >&2
    echo "What to try: sudo apt-get install -y curl bash, then run the install command again." >&2
    exit 1
  fi

  url="$RAW/$ref/deploy/freeframe"
  tmp=$(mktemp) || exit 1

  echo "Downloading the freeframe command ($ref)..."
  if ! curl -fsSL --retry 3 --max-time 60 "$url" -o "$tmp"; then
    rm -f "$tmp"
    echo "Could not download the freeframe command from $url" >&2
    echo "What to try: check that this server can reach raw.githubusercontent.com, then run the install command again. If it can, the '$ref' channel may not have the installer yet:" >&2
    echo "  curl -fsSL $RAW/latest/install.sh | sudo FREEFRAME_CHANNEL=latest bash" >&2
    exit 1
  fi
  if [ "$(head -n 1 "$tmp")" != '#!/usr/bin/env bash' ] || ! bash -n "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    echo "What was downloaded from $url is not a complete freeframe command." >&2
    echo "What to try: run the install command again in a few minutes. If it keeps happening, the '$ref' channel may not have the installer yet:" >&2
    echo "  curl -fsSL $RAW/latest/install.sh | sudo FREEFRAME_CHANNEL=latest bash" >&2
    exit 1
  fi

  # Under `curl | bash` this script's input is the download, so the
  # questions read the terminal instead, when there is one.
  rc=0
  if (: </dev/tty) 2>/dev/null; then
    bash "$tmp" install --channel "$ref" "$@" </dev/tty || rc=$?
  else
    bash "$tmp" install --channel "$ref" "$@" </dev/null || rc=$?
  fi
  rm -f "$tmp"
  exit "$rc"
}

main "$@"
