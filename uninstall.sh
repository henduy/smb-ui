#!/usr/bin/env bash
set -euo pipefail

APP_NAME="smb-ui"
INSTALL_DIR="/opt/smb-ui"
BIN_LINK="/usr/local/bin/$APP_NAME"
DOC_DIR="/usr/share/doc/$APP_NAME"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "This uninstaller needs root privileges. Re-running with sudo..." >&2
    exec sudo "$0" "$@"
fi

echo "Removing $APP_NAME..."
rm -f "$BIN_LINK"
rm -rf "$INSTALL_DIR" "$DOC_DIR"

echo "Uninstalled. Your Samba configuration files were not touched."
