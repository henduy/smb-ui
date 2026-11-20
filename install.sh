#!/usr/bin/env bash
set -euo pipefail

APP_NAME="smb-ui"
INSTALL_DIR="/opt/smb-ui"
VENV_DIR="$INSTALL_DIR/.venv"
BIN_LINK="/usr/local/bin/$APP_NAME"
DOC_DIR="/usr/share/doc/$APP_NAME"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_APP="$SCRIPT_DIR/smb-ui.py"
SRC_REQ="$SCRIPT_DIR/requirements.txt"
SRC_README="$SCRIPT_DIR/README.md"
SRC_LICENSE="$SCRIPT_DIR/LICENSE"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "This installer needs root privileges. Re-running with sudo..." >&2
    exec sudo "$0" "$@"
fi

command -v python3 >/dev/null 2>&1 || {
    echo "Error: python3 is required." >&2
    exit 1
}

for file in "$SRC_APP" "$SRC_REQ"; do
    [[ -f "$file" ]] || {
        echo "Error: missing source file: $file" >&2
        exit 1
    }
done

echo "Installing $APP_NAME to $INSTALL_DIR..."
install -d -m 755 "$INSTALL_DIR"
install -m 644 "$SRC_APP" "$INSTALL_DIR/smb-ui.py"
install -m 644 "$SRC_REQ" "$INSTALL_DIR/requirements.txt"

if [[ -f "$SRC_README" ]]; then
    install -m 644 "$SRC_README" "$INSTALL_DIR/README.md"
fi
if [[ -f "$SRC_LICENSE" ]]; then
    install -m 644 "$SRC_LICENSE" "$INSTALL_DIR/LICENSE"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

echo "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo "Creating launcher $BIN_LINK..."
cat > "$BIN_LINK" <<EOF
#!/bin/sh
exec "$VENV_DIR/bin/python" "$INSTALL_DIR/smb-ui.py" "\$@"
EOF
chmod 755 "$BIN_LINK"

if [[ -f "$SRC_README" || -f "$SRC_LICENSE" ]]; then
    install -d -m 755 "$DOC_DIR"
    if [[ -f "$SRC_README" ]]; then
        install -m 644 "$SRC_README" "$DOC_DIR/README.md"
    fi
    if [[ -f "$SRC_LICENSE" ]]; then
        install -m 644 "$SRC_LICENSE" "$DOC_DIR/LICENSE"
    fi
fi

if ! command -v testparm >/dev/null 2>&1; then
    echo >&2
    echo "Warning: 'testparm' was not found. Install it with:" >&2
    echo "  Debian/Ubuntu: sudo apt install samba-common-bin" >&2
    echo "  Fedora/RHEL:   sudo dnf install samba-common-tools" >&2
    echo "  openSUSE:      sudo zypper install samba" >&2
    echo "  Arch:          sudo pacman -S samba" >&2
fi

echo
echo "Installation complete. Start the app with: $APP_NAME"
